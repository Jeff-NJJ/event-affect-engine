# -*- coding: utf-8 -*-
"""
DataBrain 引擎：把三层串成一个闭环

 Dispositions(长期倾向)  ──┐
 State(激素/心境慢变量)  ──┼──► 评估(appraisal) ──► 情绪(v, a)
                          │                        │
                          │                        ├──► 写入 Ledger (append-only)
                          │                        ├──► 激活/反证 MemoryBucket
                          │                        └──► 反推 Dispositions/State
 长期情感 ──► 偏置新事件的解读 ──► 又一次经历 ──► ...（闭环）

关键：上行"长期情感偏置当下解读"这条边必须存在，
否则"情感决定命运"就只是一句口号而不是机制。
"""

import math
import zlib
from . import config as C
from .state import HormonalSystem, SlowState, DispositionSystem, clamp
from .memory import Ledger, MemoryBucket, ReconsolidationEngine
from .human import HumanLayer, is_open_loop
from .agentface import AgentFace, DelayedReleaser, USER_AFFECT_EFFECTS


# 不同刺激的基础重要度（1-10 量表，与 Ombre-Brain schema 对齐）
STIM_IMPORTANCE = {
    'betrayal': 9, 'crisis': 10, 'humiliation': 8, 'loss': 9,
    'conflict': 7, 'rejection': 7, 'criticism': 6, 'provocation': 5,
    'reconciliation': 8, 'affection': 6, 'shared_joy': 6, 'gratitude': 5,
    'praise': 5, 'apology': 5, 'playful_neg': 3, 'curiosity': 3,
    'neglect': 4, 'loneliness': 4, 'neutral': 2, 'mundane': 1,
}

# 社会化刺激（会受催产素额外调制）
SOCIAL_STIMS = {'affection', 'praise', 'gratitude', 'apology', 'shared_joy',
                'reconciliation', 'playful_neg', 'conflict', 'betrayal',
                'rejection', 'humiliation', 'neglect', 'criticism'}


def scene_of(hour):
    """把一天切成几个生活场景，主题才有场景维度。"""
    if hour is None:
        return '日常'   # 审查C1: 语料没有可信钟点时不再伪造场景归属
    if 5 <= hour < 9:
        return '早晨'
    if 9 <= hour < 18:
        return '工作'
    if 18 <= hour < 23:
        return '晚间'
    return '深夜'


def sanitize_subject(name):
    """客体名清洗（审查B7/安全根因）：语料昵称是对端可控字符串。
    剥掉路径分隔符与控制字符，限长 —— 主题键分隔符、文件路径、
    前端模板都依赖它干净。

    二轮审查补充：纯删除式清洗会把两个不同的真人合并成一个记忆桶
    （实测 '测试/甲' 与 '测试甲' 同键，contact_count 混算）。
    凡清洗改变了原名的，一律附加原名哈希后缀 —— 清洗不再丢身份；
    本来就干净的名字保持可读、不加后缀。"""
    raw = str(name or '')
    s = raw.strip()
    # 控制字符（\n \t \r 等 isprintable()==False 的一律排除）与尖括号/引号剥掉：
    # 换行能伪造 prompt_suffix 的行结构（审查A6），尖括号与引号是 XSS 的原料（审查A2）。
    # 保留 ' 与 & —— 它们是正常姓名用字（O'Brien），且输出侧 esc() 已覆盖。
    # 这是第二道防线，前端 esc() 仍然是必需的。
    s = ''.join(ch for ch in s
                if ch.isprintable()
                and ch not in '|/\\\x00'
                and ch not in '<>"')
    s = s.replace('..', '.').strip()[:40]
    if not s:
        s = '未知'
    # 清洗是否动过原名（含限长截断）？动过就加后缀保身份。
    # 用 crc32 而非内置 hash()：后者带进程级随机种子，会毁掉可复现性。
    if raw.strip() != s:
        digest = format(zlib.crc32(raw.strip().encode('utf-8')) & 0xFFFFFFFF, '08x')
        s = f"{s[:24]}·{digest}"
    return s


def theme_key_of(subject, stim, hour):
    """主题 = (客体, 刺激类别, 场景)。"汤姆在工作场合伤害我" 就是一个主题。"""
    grp = C.STIM_GROUP.get(stim, 'neutral')
    return f"{subject}|{grp}|{scene_of(hour)}"


class DataBrain:
    def __init__(self, seed=0):
        import random
        self.rng = random.Random(seed)
        self.hormones = HormonalSystem()
        self.slow = SlowState()
        self.disp = DispositionSystem()
        self.ledger = Ledger()
        self.recon = ReconsolidationEngine()
        self.human = HumanLayer(seed)
        self.person_buckets = {}       # subject -> MemoryBucket（历史认定）
        self.themes = {}               # theme_key -> MemoryBucket（模式认定）
        self._theme_counts = {}        # 尚未升格为模式的主题计数
        self.episodic = []             # 高显著性情节记忆
        self._bucket_seq = 1
        self.t = 0.0
        self.events_processed = 0
        self.timeline = []             # 按天采样，供可视化用
        # 面向 agent 的输出层（自原内分泌引擎 v2.0 移植）
        self.face = AgentFace()
        self.delayed = DelayedReleaser()

    # =========================================================
    # 评估：把一次具体经历变成情绪
    # =========================================================
    def appraise(self, subject, stim, intensity=1.0, bias_theme=None, habit=1.0,
                 override_va=None):
        """
        情绪(t) = 事件属性 × 倾向命中 × 当下状态偏置 × 既有模式的同化

        这一步输出的是"此刻的感受"，不是"对这个人的长期认定"

        bias_theme: 已经成形的相关模式。**确认偏误**——一旦某人被认定为
        "在工作场合会伤害我"，之后同类情境下的模糊信号就会被朝这个方向解读。
        这条边是"命运"的真正机制：早期的模式会改写你之后看到的世界。

        override_va: 外部已经打好的情感坐标（接 Ombre-Brain 时用）。
        它只替换"这件事本身"的坐标，后面的倾向放电、心境偏置、确认偏误
        照样照常起作用 —— 别人给的是原始素材，怎么解读仍然是这个大脑的事。
        """
        base_v, base_a = C.STIMULUS_VA.get(stim, (0.5, 0.3))
        if override_va is not None:
            ov, oa = override_va
            if ov is not None:
                base_v = clamp(float(ov), 0.0, 1.0)
            if oa is not None:
                base_a = clamp(float(oa), 0.0, 1.0)

        # --- 显著性：决定能不能按到别人的按钮 ---
        # habit = 习惯化因子：同样的刺激重复太多次，就按不动了
        salience = clamp(base_a * 0.6 + abs(base_v - 0.5) * 2.0 * 0.4, 0.0, 1.0) * intensity * habit

        # --- 倾向放电 ---
        hits = self.disp.evaluate(stim, salience)
        v = base_v
        if hits:
            total_charge = sum(h['charge'] for h in hits)
            # 多个按钮同时被按：加权平均它们的情感色调
            bias = sum(h['val_bias'] * h['charge'] for h in hits) / max(total_charge, C.EPS)
            v += bias * clamp(total_charge, 0.0, 1.5)

        # --- 上行闭环：长期情感偏置当下解读 ---
        #   这就是"为什么同一个人在顺境和逆境下对同一句话反应不同"
        mood_v = self.slow.v['mood_valence']
        state_v = self.hormones.valence_proxy()
        v += 0.12 * (mood_v - 0.5) + 0.08 * (state_v - 0.5)

        # --- 熟悉的客体更容易被强烈解读（关系越深，波动越大）---
        pb = self.person_buckets.get(subject)
        if pb is not None:
            bond = self.slow.v['rel_temp']
            v += 0.06 * (bond - 0.5) * (1.0 if v > pb.valence else -1.0)

        # --- 唤醒：事件本身 + 激素状态 ---
        a = clamp(base_a * intensity + 0.25 * self.hormones.arousal +
                  sum(h['charge'] for h in hits) * 0.15, 0.0, 1.0)

        # --- 既有模式的同化（确认偏误）---
        if bias_theme is not None:
            ramp = clamp((bias_theme.support_count - 1) / C.THEME['confirmation_ramp'], 0.0, 1.0)
            k = C.THEME['confirmation_max'] * ramp
            v += k * (bias_theme.valence - v)

        # --- 强度要把情感【推向极端】，而不是只改变唤醒 ---
        # 同一类事件，程度不同，感受的强度必须不同：
        # "救命之恩" 和 "举手之劳" 都属于 reconciliation，但不该产生同样的效价。
        # 不修这里的话，intensity 只能影响 arousal 和 salience，
        # 而对"这一刻我有多被触动"毫无作用 —— 那它是没有生命力的参数。
        v = 0.5 + (v - 0.5) * (0.55 + 0.45 * intensity)

        return clamp(v, 0.0, 1.0), clamp(a, 0.0, 1.0), clamp(salience, 0.0, 1.0), hits

    # =========================================================
    # 主入口：经历一件事
    # =========================================================
    def experience(self, t_sim, subject, stim, text='', intensity=1.0,
                   override_va=None, hour=None):
        """hour: 真实钟点（0-24），None = 不可信（审查C1）。
        不可信时：昼夜调制走全天均值（见 state.HormonalSystem.tick），
        场景维度退化为 '日常'，不再用假小时驱动节律与主题键。"""
        subject = sanitize_subject(subject)
        trusted_hour = hour
        self._advance_to(t_sim, hour=trusted_hour)
        tk = theme_key_of(subject, stim, trusted_hour)
        theme = self.themes.get(tk)
        # 只有已经「升格为模式」的主题才产生同化作用
        bias = theme if (theme is not None
                         and theme.support_count >= C.THEME['min_support']) else None
        # 习惯化：这次的刺激是不是已经听过太多次了（按客区分账，审查B8）
        habit = self.human.note_stim(stim, t_sim, subject)
        v, a, salience, hits = self.appraise(subject, stim, intensity, bias, habit,
                                             override_va)

        # 人味层：情绪不是瞬变的，受惯性与不应期约束
        # --- 原内分泌引擎 v2.0 移植过来的三件事 ---
        # 1) 对方状态识别：他说自己生气/难受，会带动我们的激素
        affects = []
        if C.AGENT['user_affect_enabled']:
            affects = self.face.detect_user_affects(text)
            self.face.user_affects = affects
            for af in affects:
                self.hormones.apply_raw(USER_AFFECT_EFFECTS.get(af, {}),
                                        C.AGENT['user_affect_scale'])
        # 2) 对话节奏 + 分离焦虑计时原点
        self.face.note_contact(subject, t_sim, self.hormones)
        # 修复（审查B4）：语气惯性与行为冷却只在**真实互动**时落子/消耗，
        # 读取（prompt_suffix）不再改动它们。
        self.face.tone(self.hormones, persist=True)
        self.face.urges(self.hormones, consume=True)
        # 3) 慢路径：不是当场全部释放，排进时间轴
        sp = self.face.maybe_slow_path(stim, self.hormones, text)
        if sp:
            self.delayed.schedule(t_sim, sp['effects'],
                                  f'slow_path:{sp["state"]}/{sp["branch"]}', subject)

        ev_v, ev_a = self.human.push_emotion(v, a, t_sim)

        # ---- 躯体反应 ----
        self.hormones.apply_stimulus(stim, intensity)
        # 负性社交经历会攒背景压力（"最近怎么事事不顺"）
        self.slow.on_social_stress(v, salience)
        # ---- 慢变量：关系温度只在社会性互动里被推动 ----
        if stim in SOCIAL_STIMS:
            base_delta = (v - 0.5) * 0.02 * salience
            self.slow.on_affection(base_delta, self.hormones)

        # ---- 编码调制：把"此刻的身体状态"写进这条记忆的深度 ----
        mod = self.hormones.encoding_modulation(is_social=(stim in SOCIAL_STIMS))
        # 认知门控（倒 U）：唤醒过高或过低时，新东西就是写不深。
        # 原版定义了这个阈值但从没用过，我们真正接上了。
        mod *= self.face.encoding_gate(self.hormones)

        # ---- 落到账本先占位（保证 bucket_id 可回填）----
        ev = self.ledger.append(
            id=None, t=t_sim, subject=subject, stim=stim, text=text,
            salience=salience, v=v, a=a, hits=hits, bucket_id=None, mod=mod)
        self.events_processed += 1

        # ---- 历史认定（subject 级）：创建 or 反证 ----
        if subject not in self.person_buckets:
            imp = STIM_IMPORTANCE.get(stim, 4) * mod * (0.6 + 0.4 * salience)
            b = MemoryBucket(
                id=f"P:{subject}", subject=subject, t0=t_sim,
                valence=v, arousal=a,
                importance=clamp(imp, 1, 10), origin_event_id=ev.id)
            b.is_person = True
            self.person_buckets[subject] = b
            self._bucket_seq += 1
            res = {'t': t_sim, 'bucket': b.id, 'action': 'created',
                   'detail': f'初次印象 v={v:.3f} importance={imp:.2f}'}
        else:
            bucket = self.person_buckets[subject]
            bucket.last_active_t = t_sim
            bucket.last_contact_t = t_sim      # 这是一次真实的联系
            # 陪伴累积：不管这次够不够格改写判断，处一次就熟一点
            bucket.reset_cooling()
            bucket.on_contact(v, salience, t_sim)
            res = self.recon.offer(bucket, ev, t_sim)

        pb = self.person_buckets[subject]
        pb.event_ids.append(ev.id)
        ev.bucket_id = pb.id
        # 矛盾情感：同时攒下正性和负性证据，态度才会摇摆
        if v > 0.6:
            pb.pos_count += 1
        elif v < 0.4:
            pb.neg_count += 1
        if theme is not None:
            if v > 0.6:
                theme.pos_count += 1
            elif v < 0.4:
                theme.neg_count += 1
        self._last_appraisal = res

        # ---- 主题层：同类事件累积到一定次数才升格为"模式" ----
        theme_action = 'none'
        if tk in self.themes:
            th = self.themes[tk]
            th.support_count += 1
            th.last_active_t = t_sim
            theme_action = self.recon.offer(th, ev, t_sim)['action']

            # ---- 跨主题横向反证 ----
            # 汤姆在工作场合的善意，会去软化 "汤姆|敌意|工作" 这个模式。
            # 少了这条，主题层内部永远同向，模式会变成永不改变的死结。
            # 解析方式修复（审查B7）：键形如 subject|grp|scene，subject 已经
            # sanitize 过不含 '|'，用 split('|') 三段解，不再用 rsplit(2) 猜位。
            parts = tk.split('|')
            grp = parts[1] if len(parts) == 3 else C.STIM_GROUP.get(stim, 'neutral')
            scene = parts[2] if len(parts) == 3 else '日常'
            for other_key, other in self.themes.items():
                if other_key == tk:
                    continue
                op = other_key.split('|')
                if len(op) != 3:
                    continue
                o_subj, o_grp, o_scene = op
                if o_subj != subject or o_scene != scene or o_grp == grp:
                    continue
                self.recon.offer_cross(other, ev, t_sim,
                                       C.THEME['cross_weight'])
        else:
            # 修复（审查B9）：升格不再只取"触发升格的那一条"的属性做起点，
            # 而是聚合全部支撑事件（均值定 valence/arousal，取 max 定 importance）。
            # 账本里本来就有这些时刻，折叠成一个 support_count 是浪费。
            rec = self._theme_counts.get(tk)
            if rec is None:
                rec = {'n': 0, 'members': []}
                self._theme_counts[tk] = rec
            rec['n'] += 1
            imp0 = STIM_IMPORTANCE.get(stim, 4) * mod * (0.6 + 0.4 * salience)
            rec['members'].append((v, a, imp0))
            if rec['n'] >= C.THEME['min_support']:
                mv = sum(m[0] for m in rec['members']) / rec['n']
                ma = sum(m[1] for m in rec['members']) / rec['n']
                mi = max(m[2] for m in rec['members'])
                th = MemoryBucket(
                    id=f"T:{tk}", subject=subject, t0=t_sim,
                    valence=mv, arousal=ma, importance=clamp(mi, 1, 10),
                    origin_event_id=ev.id)
                th.theme_members = list(rec['members'])
                th.is_theme = True
                th.theme_key = tk
                th.support_count = rec['n']
                th.extra_impedance = C.THEME['extra_impedance']
                th.update_scale = C.THEME['update_rate_scale']
                self.themes[tk] = th
                self._bucket_seq += 1
                theme_action = 'formed'

        # ---- 情节记忆：够格的事才单独成桶 ----
        # 门槛不能太高，否则记忆层只剩 subject 级的摘要——
        # 那恰好是我们最反对的"结论取代了构成它的时刻"。
        if salience >= 0.42:
            imp = STIM_IMPORTANCE.get(stim, 4) * mod * (0.6 + 0.4 * salience)
            eb = MemoryBucket(
                id=f"E{self._bucket_seq}:{stim}", subject=subject, t0=t_sim,
                valence=v, arousal=a, importance=clamp(imp, 1, 10),
                origin_event_id=ev.id)
            # Zeigarnik：没说完、没解决的事会一直惦记着
            eb.open_loop = is_open_loop(stim)
            self._bucket_seq += 1
            self.episodic.append(eb)

        return {'v': v, 'a': a, 'salience': salience, 'mod': mod,
                'hits': [h['name'] for h in hits], 'action': res['action'],
                'detail': res.get('detail', ''),
                'theme': tk, 'theme_action': theme_action,
                # 实际"说出来"的情绪（带惯性与不应期），而不是瞬时评估值
                'emo_v': ev_v, 'emo_a': ev_a, 'habit': habit}

    # =========================================================
    # 时间推进
    # =========================================================
    def _advance_to(self, t_sim, hour=None):
        dt = t_sim - self.t
        if dt > 0:
            self.tick(t_sim, dt, hour=hour)

    def tick(self, t_sim, dt_hours, hour=None):
        """hour=None: 昼夜调制取全天均值（审查C1：语料只有天精度时不伪造相位）。
        dt>=24h 时按日积分，但内部只做一次检查（审查B11：长步长下检查成本与步长成反比）。"""
        if not (dt_hours > 0):
            return
        self.hormones.tick(t_sim, dt_hours, hour=hour)
        self.slow.tick(dt_hours, self.hormones)
        self.disp.tick(t_sim, dt_hours)

        # ---- 人味层：静默期的三件事 ----
        #  1. 情绪向静息水位回落
        self.human.tick_emotion(t_sim, dt_hours,
                                rest_v=self.slow.v['mood_valence'],
                                rest_a=self.slow.v['mood_arousal'])
        #  2. 没事的时候也会想起某人
        all_b = (list(self.person_buckets.values())
                 + list(self.themes.values()) + self.episodic)
        self.human.maybe_spontaneous_recall(all_b, t_sim, dt_hours)

        # 延迟到点的激素反应（慢路径）
        released = self.delayed.tick(t_sim, self.hormones)
        if released:
            self.face.released.extend(released)

        for b in self.person_buckets.values():
            b.tick(t_sim, dt_hours)
            # 疏远冷却：久不联系，这个人在心里会慢慢变淡、变没那么笃定
            b.cool(dt_hours, t_sim)
            # 分离焦虑：同一件事的另一面 —— 越在意的人，消失越久越难受。
            # 冷却让它变淡，焦虑让它此刻难受，两条并行不矛盾。
            # 修复（1012审查）：分离焦虑只给真人算。
            # 外部_桶是事件通道的记账（world/galatea/spam），它们天然低频，
            # 拿"90小时没更新"当"被这个人抛弃"是范畴错误，
            # 会把皮质醇慢性抬到过载线。
            if not str(b.subject).startswith('外部_'):
                self.face.tick_separation(t_sim, b.subject, b.valence, self.hormones)
            if not b.archived and not b.pinned and b.score(t_sim) < C.DECAY['threshold']:
                b.archived = True
        for b in self.themes.values():
            b.tick(t_sim, dt_hours)
            self.human.tick_fab(b, dt_hours)
            if not b.archived and b.score(t_sim) < C.DECAY['threshold']:
                b.archived = True
        for b in self.episodic:
            b.tick(t_sim, dt_hours)
            #  FAB：负性记忆的情感会比正性衰减得更快
            self.human.tick_fab(b, dt_hours)
            if not b.archived and b.score(t_sim) < C.DECAY['threshold']:
                b.archived = True
        self.t = t_sim

    # =========================================================
    # 读取
    # =========================================================
    def recall(self, t_now, top_k=5):
        """按当前 score 浮现记忆（模拟"这一刻会想起谁"）。"""
        all_b = [b for b in (list(self.person_buckets.values())
                             + list(self.themes.values()) + self.episodic)
                 if not b.archived]
        all_b.sort(key=lambda b: b.score(t_now), reverse=True)
        return all_b[:top_k]

    def snapshot(self, t_now):
        return {
            'day': round(t_now / 24.0, 2),
            'hormones': {k: round(v, 4) for k, v in self.hormones.h.items()},
            'arousal': round(self.hormones.arousal, 4),
            'cognitive_gain': round(self.hormones.cognitive_gain(), 4),
            'slow': {k: round(v, 4) for k, v in self.slow.v.items()},
            'disp': {k: round(v, 4) for k, v in self.disp.activation.items() if v > 1e-3},
            'buckets': [b.to_dict(t_now) for b in self.person_buckets.values()],
            'themes': [b.to_dict(t_now) for b in self.themes.values()],
            'events_total': len(self.ledger),
        }

    # =========================================================
    # 轨迹采样：给可视化用
    # =========================================================
    def record_timeline(self, t_now):
        """每天调一次，把各层的状态按天采样成一条曲线。"""
        point = {
            'day': round(t_now / 24.0, 3),
            'hormones': {k: round(v, 4) for k, v in self.hormones.h.items()},
            'arousal': round(self.hormones.arousal, 4),
            'cognitive_gain': round(self.hormones.cognitive_gain(), 4),
            'mood_valence': round(self.slow.v['mood_valence'], 4),
            'mood_arousal': round(self.slow.v['mood_arousal'], 4),
            'rel_temp': round(self.slow.v['rel_temp'], 4),
            'persons': {s: {
                'v': round(b.valence, 4),
                'a': round(b.arousal, 4),
                'imp': round(b.impedance(t_now), 4),
                'score': round(b.score(t_now), 3),
            } for s, b in self.person_buckets.items()},
            'themes': {k: {
                'v': round(b.valence, 4),
                'a': round(b.arousal, 4),
                'support': b.support_count,
                'imp': round(b.impedance(t_now), 4),
            } for k, b in self.themes.items()},
        }
        self.timeline.append(point)
        return point

    # =========================================================
    # 导出：给可视化用
    # =========================================================
    # =========================================================
    # 给 agent 的输出
    # =========================================================
    def prompt_suffix(self, subject=None):
        """
        拿到一段可以直接拼进 LLM 提示词的状态文本。
        这是「接进 agent」的接口 —— 传 subject 就会带上对这个人的认定。
        """
        return self.face.prompt_suffix(self, subject, self.t)

    def export(self, t_now, max_events=2000):
        """产出一份完整的时间序列 + 结构快照，供前端渲染。"""
        themes = []
        for b in self.themes.values():
            d = b.to_dict(t_now)
            d['theme_key'] = b.theme_key
            d['support_count'] = b.support_count
            themes.append(d)
        return {
            'meta': {
                't_end': t_now,
                'days': round(t_now / 24.0, 2),
                'events_total': len(self.ledger),
                'persons': len(self.person_buckets),
                'themes': len(self.themes),
                'episodic': len(self.episodic),
                'revisions': sum(len(b.appraisal_chain)
                                 for b in list(self.person_buckets.values())
                                 + list(self.themes.values())),
            },
            'persons': [b.to_dict(t_now) for b in self.person_buckets.values()],
            'themes': themes,
            'episodic': [b.to_dict(t_now) for b in self.episodic[:60]],
            'timeline': self.timeline,
            'events': [e.to_dict() for e in self.ledger.events[:max_events]],
            'recon_log': self.recon.log,
        }
