# -*- coding: utf-8 -*-
"""
Ledger 层：append-only 事件账本 + 记忆桶 + 记忆衰减 + 再巩固

------------------------------------------------------------------
这一层承载杰夫的汤姆论证：
    「历史认定」看起来只是一个结论，但它的本体不可压缩。

由此产生三条硬约束，在本文件里逐条落实：

  1. Event 永远 append。不存在 update_event / delete_event 这两个方法。
  2. 摘要 (valence/arousal 当前值) 只是**派生索引**，永远可以从
     appraisal_chain + event_ids 走回产生它的原始时刻。
  3. 更新 = 重新加权，不是覆盖。原始 appraise (valence0/arousal0)
     在对象生命周期内只读，永不被赋值。

------------------------------------------------------------------
再巩固的设计要点（杰夫拍板：允许更新，但必须有经历作为触发条件）：

  门控(3道) → 阻抗(3项) → 累积(带衰减) → 窗口(限幅) → 版本链(留痕)

  单次事件永远不能翻转一条深记忆。要翻转，必须**攒够反证据**，
  而攒的速度取决于这条记忆有多深。
"""

import math
from . import config as C
from .state import clamp, decay_factor


# ============================================================
# 事件：append-only
# ============================================================
class Event:
    __slots__ = ('id', 't', 'subject', 'stim', 'text', 'salience',
                 'v', 'a', 'hits', 'bucket_id', 'mod')

    def __init__(self, id, t, subject, stim, text, salience, v, a, hits, bucket_id, mod):
        self.id = id; self.t = t; self.subject = subject; self.stim = stim
        self.text = text; self.salience = salience; self.v = v; self.a = a
        self.hits = hits; self.bucket_id = bucket_id; self.mod = mod

    def to_dict(self):
        return {'id': self.id, 't': round(self.t, 3), 'subject': self.subject,
                'stim': self.stim, 'text': self.text,
                'salience': round(self.salience, 4), 'v': round(self.v, 4),
                'a': round(self.a, 4),
                # 前端要显示给人看，用中文名而不是内部 id
                'hits': [h.get('name', h.get('id', '')) for h in self.hits],
                'bucket_id': self.bucket_id, 'mod': round(self.mod, 4)}


class Ledger:
    """只增不改的事件账本。"""
    def __init__(self):
        self.events = []
        self._next_id = 1

    def append(self, **kw):
        # id 由账本自己分配，调用方传来的 id 一律忽略（防止伪造/重复）
        kw.pop('id', None)
        e = Event(id=self._next_id, **kw)
        self._next_id += 1
        self.events.append(e)
        return e

    def __len__(self):
        return len(self.events)

    def integrity_check(self):
        """自检：id 必须严格递增且连续，时间戳必须单调不减。"""
        problems = []
        ids = [e.id for e in self.events]
        if ids != list(range(1, len(ids) + 1)):
            problems.append(f"事件 id 不连续: {ids[:10]}...")
        ts = [e.t for e in self.events]
        for i in range(1, len(ts)):
            if ts[i] < ts[i - 1] - 1e-6:
                problems.append(f"时间戳回退于 #{i}: {ts[i-1]} -> {ts[i]}")
                break
        return problems


# ============================================================
# 记忆桶
# ============================================================
class MemoryBucket:
    def __init__(self, id, subject, t0, valence, arousal, importance, origin_event_id):
        self.id = id
        self.subject = subject
        self.created_t = t0
        self.last_active_t = t0     # 最后一次被"提取"（含自发回忆）
        self.last_contact_t = t0    # 最后一次被"联系" —— 必须和提取分开：
                                    # 想起一个人不等于联系了他，否则疏远冷却永远是死代码
        self.activation_count = 1

        # --- 当前评估（可被重写）---
        self.valence = clamp(valence, 0.0, 1.0)
        self.arousal = clamp(arousal, 0.0, 1.0)
        # --- 原始评估（只读，永不被赋值）---
        self.valence0 = self.valence
        self.arousal0 = self.arousal

        self.importance = clamp(importance, 1, 10)
        self.resolved = False
        self.pinned = False
        self.archived = False

        # --- 再巩固状态 ---
        self.counter_evidence = 0.0
        self.last_recon_t = None
        self.appraisal_chain = []      # 每次重写都留痕
        self.origin_event_id = origin_event_id
        self.event_ids = [origin_event_id]

        # --- 隧道记忆：外围细节保真度 ---
        self.detail_fidelity = 1.0

        # --- 主题层专用（普通桶保持默认即等价）---
        self.is_theme = False
        self.theme_members = []        # 审查B9: 升格前累积的支撑事件 (v,a,imp)，聚合定起点
        self.is_person = False
        self.theme_key = None
        self.support_count = 1
        self.extra_impedance = 0.0     # 模式比单次事件更顽固
        self.update_scale = 1.0        # 模式被改写得更慢

        # --- 人味层专用 ---
        self.pos_count = 1 if valence > 0.6 else 0    # 正性证据计数
        self.neg_count = 1 if valence < 0.4 else 0    # 负性证据计数
        self.open_loop = False         # 未完成的环路（Zeigarnik）

        # --- 熟悉度层（陪伴累积 / 疏远冷却）---
        # fam_drift 只记"陪伴"造成的那部分漂移，和再巩固的改写分开记账
        self.fam_drift = 0.0
        self.contact_count = 1
        self.cool_days = 0.0      # 当前这段连续疏远的天数（联系即清零）
        self.cool_total = 0.0     # 累计疏远天数（不清零 —— 端点统计用，
                                  # 否则冷却会在每次联系后"洗白"，看不出它干过活）
        self.imp_relief = 0.0     # 疏远带来的阻抗松动

    # --------------------------------------------------------
    # 陪伴累积：高频低强度的接触也会慢慢改变态度
    # --------------------------------------------------------
    def on_contact(self, v, salience, t_now=None):
        """
        一次接触之后的隐性态度漂移。

        和"再巩固"的区别：这里不设门槛、不记版本链、不算改写次数。
        它没有"某一刻改主意了"这种瞬间 —— 就是处久了自然变了。

        学习率取 Rescorla-Wagner 形式：Δ = α·salience·(target − 现在)，
        α 随阻抗衰减。所以一次够重的经历（背叛、道歉）推得动一个老熟人，
        而流水账式的寒暄只能慢慢把人焐热。
        """
        f = C.FAMILIARITY
        self.contact_count += 1
        # 修复(审查B2): 重新有联系=记忆又活了, 必须解除归档。原版 archived 单向卡死:
        # real.json 里 3/5 核心人物 score 高达 9.7~24.7 却永久 archived=True,
        # 等于大脑"想不起"自己最在意的人。
        self.archived = False
        # 这次接触"指向"的态度：内容好坏 × 耦合 + 见面本身带来的暖意
        target = clamp(0.5 + (v - 0.5) * f['valence_coupling']
                       + f['exposure_bias'], 0.0, 1.0)
        sw = max(salience, 0.0) ** f['salience_exp']
        imp = self.impedance(t_now if t_now is not None else self.last_active_t)
        lr = f['gain'] * sw / (1.0 + f['impedance_damp'] * imp)
        step = lr * (target - self.valence)
        # 累积幅度封顶
        if abs(self.fam_drift + step) > f['max_drift']:
            room = f['max_drift'] - abs(self.fam_drift)
            step = math.copysign(max(0.0, room), step)
        self.fam_drift += step
        self.valence = clamp(self.valence + step, 0.0, 1.0)
        return step

    # --------------------------------------------------------
    # 疏远冷却：久不联系，态度回落到中性，阻抗也跟着松动
    # --------------------------------------------------------
    def cool(self, dt_hours, t_now):
        """
        只对社会性记忆（客体层）有意义。
        长期没有互动 → 认定往中性回落，而且这个人重新变得"可以被改变"
        —— 这符合直觉：很久没见的人，你对他的看法没那么根深蒂固。
        """
        f = C.FAMILIARITY
        idle_days = (t_now - self.last_contact_t) / 24.0
        dt_days = dt_hours / 24.0
        if idle_days <= f['neglect_days']:
            return 0.0
        # 超出宽限期的部分按天冷却
        self.cool_days += dt_days
        self.cool_total += dt_days
        pull = f['neutral_point'] - self.valence
        step = pull * min(0.5, f['cool_rate'] * dt_days)
        self.valence = clamp(self.valence + step, 0.0, 1.0)
        self.fam_drift = clamp(self.fam_drift + step,
                               -f['max_drift'], f['max_drift'])
        self.imp_relief = min(f['cool_imp_relief'],
                              self.imp_relief + f['cool_imp_rate'] * dt_days)
        return step

    def reset_cooling(self):
        """重新联系上了：冷却积累清零，阻抗恢复。"""
        if self.cool_days > 0.0:
            self.cool_days = 0.0
            self.imp_relief = 0.0

    # --------------------------------------------------------
    # 阻抗：这条记忆有多难被改写
    # --------------------------------------------------------
    def impedance(self, t_now):
        """
        三项相加：
          age      —— 固化越久越难动（但有饱和，不是线性无限增长）
          arousal  —— 原始唤醒越高越难动（情绪性记忆的顽固性）
          repeat   —— 被反复提取过越多次越难动（每次提取都是一次再固化）
        """
        r = C.RECONSOLIDATION
        age_days = max(0.0, (t_now - self.created_t) / 24.0)
        # 平滑饱和，避免长时间模拟下无限增大
        age_factor = r['imp_age_max'] * (1.0 - math.exp(-age_days / max(r['imp_age_scale'], C.EPS)))
        arousal_factor = self.arousal0 * r['imp_arousal_weight']
        repeat_factor = min(r['imp_repeat_max'],
                            max(0, self.activation_count - 1) * r['imp_repeat_weight'])
        base = (age_factor + arousal_factor + repeat_factor
                + self.extra_impedance)
        # 疏远会松动阻抗：很久没联系的人，看法没那么根深蒂固
        return clamp(base - self.imp_relief, 0.0, 10.0)

    def memory_depth(self, t_now):
        """给外部读取的"深度"指标：0=浅，1+=深。用于报告与可视化。"""
        return self.impedance(t_now)

    # --------------------------------------------------------
    # 记忆衰减（扩展版 Ombre-Brain 公式）
    # --------------------------------------------------------
    def score(self, t_now):
        """
        原版:  importance × act^0.3 × e^(-λ·d) × (base + arousal·boost) × time_weight
        扩展:  +(1) 效价偏离度加成
              +(2) 隧道效应：负性高唤醒 → 细节保真度额外受损
        """
        d = C.DECAY
        if self.pinned:
            return 999.0
        days = max(0.0, (t_now - self.last_active_t) / 24.0)

        # 分段时间权重
        tw = d['time_weight']
        if days <= 1.0:
            time_weight = tw['d1']
        elif days <= 2.0:
            time_weight = tw['d1'] - (tw['d1'] - tw['d2']) * (days - 1.0)
        else:
            time_weight = max(tw['floor'], tw['d2'] * math.exp(-tw['k'] * (days - 2.0)))

        # >>> 扩展 1：效价偏离度 <<<
        dev = abs(self.valence - 0.5) * 2.0      # 0..1
        emotion_weight = (d['emotion_base']
                          + self.arousal * d['arousal_boost']
                          + dev * d['valence_boost'])

        act = max(1, self.activation_count)
        # 提取次数的收益必须有上限：否则长期陪伴会让 score 无限增长，
        # 结果是"见过最多次的人永远不可能被遗忘"——这在动力学上是失控的。
        act_gain = min(3.0, act ** 0.3)
        base_score = (self.importance
                      * act_gain
                      * math.exp(-d['lambda'] * days)
                      * emotion_weight)

        resolved_factor = d['resolved_factor'] if self.resolved else 1.0
        urgency = (d['urgency_boost']
                   if (self.arousal > d['urgency_arousal_gate'] and not self.resolved)
                   else 1.0)
        # Zeigarnik：没完成的事一直惦记着，衰减更慢
        open_boost = (C.HUMAN['zeigarnik']['boost']
                      if (self.open_loop and not self.resolved) else 1.0)
        return round(time_weight * base_score * resolved_factor * urgency * open_boost, 4)

    def tick(self, t_now, dt_hours):
        """
        每个 tick 要做三件事：
          1. 反证据随时间衰减（轻微的反驳会被时间冲淡，不留永久账）
          2. 隧道效应下的细节丢失
          3. 自动归档判定
        """
        r = C.RECONSOLIDATION
        if dt_hours > 0 and self.counter_evidence > 0:
            f = decay_factor(dt_hours, r['evidence_decay_half_life'])
            self.counter_evidence *= f
            if self.counter_evidence < 1e-4:
                self.counter_evidence = 0.0

        d = C.DECAY
        if self.arousal > d['tunnel_arousal_gate'] and self.valence < d['tunnel_valence_gate']:
            loss = d['tunnel_detail_loss'] * dt_hours / 24.0
            self.detail_fidelity = max(0.05, self.detail_fidelity - loss)
        else:
            # 细节不会自己回来，只是慢下来
            pass

    def to_dict(self, t_now):
        return {
            'id': self.id, 'subject': self.subject,
            'created_day': round(self.created_t / 24.0, 2),
            'age_days': round((t_now - self.created_t) / 24.0, 2),
            'valence': round(self.valence, 4), 'arousal': round(self.arousal, 4),
            'valence0': round(self.valence0, 4), 'arousal0': round(self.arousal0, 4),
            'drift': round(self.valence - self.valence0, 4),
            'importance': round(self.importance, 2),
            'activation_count': self.activation_count,
            'resolved': self.resolved, 'archived': self.archived,
            'score': round(self.score(t_now), 4),
            'impedance': round(self.impedance(t_now), 4),
            'counter_evidence': round(self.counter_evidence, 4),
            'revisions': len(self.appraisal_chain),
            'detail_fidelity': round(self.detail_fidelity, 3),
            # 熟悉度层：陪伴带来的隐性漂移 / 疏远带来的冷却
            'fam_drift': round(self.fam_drift, 4),
            'contact_count': self.contact_count,
            'cool_days': round(self.cool_days, 1),
            'cool_total': round(self.cool_total, 1),
            'imp_relief': round(self.imp_relief, 3),
        }


# ============================================================
# 再巩固：经历驱动的旧判断更新
# ============================================================
def evidence_threshold(imp):
    """
    反证据阈值。阻抗的影响**封顶且次线性**——否则记忆一深就永远锁死。
    """
    r = C.RECONSOLIDATION
    return r['evidence_threshold_base'] * (1.0 + min(imp, r['imp_threshold_cap'])
                                           * r['imp_threshold_slope'])


class ReconsolidationEngine:
    """
    门控 → 阻抗 → 累积 → 窗口 → 留痕

    返回结构化日志，便于事后回放检查"为什么它在这一天改了主意"。
    """

    def __init__(self):
        self.log = []
        self.warnings = []

    # --------------------------------------------------------
    def offer(self, bucket, ev, t_now):
        """
        ev: Event —— 一次新的具体经历。
        返回 dict 描述本次发生了什么（含 'action' 字段）。
        """
        r = C.RECONSOLIDATION
        out = {'t': t_now, 'bucket': bucket.id, 'subject': bucket.subject,
               'action': 'none', 'detail': ''}

        # ===== 门控 1：客体必须一致 =====
        if r['subject_match_required'] and bucket.subject != ev.subject:
            out['detail'] = '客体不匹配'
            return out

        # ===== 门控 2：记忆必须有足够历史 =====
        age_h = t_now - bucket.created_t
        if age_h < r['min_age_hours']:
            out['detail'] = f'记忆过新({age_h:.1f}h < {r["min_age_hours"]}h)'
            out['action'] = 'too_young'
            return out

        # ===== 门控 3：事件显著性够格 =====
        if ev.salience < r['min_salience']:
            out['detail'] = f'显著性不足({ev.salience:.3f})'
            out['action'] = 'too_weak'
            return out

        # ===== 门控 4：冷却期 =====
        if bucket.last_recon_t is not None:
            since = t_now - bucket.last_recon_t
            if since < r['cooldown_hours']:
                out['detail'] = f'冷却中({since:.1f}h < {r["cooldown_hours"]}h)'
                out['action'] = 'cooling'
                return out

        # ===== 同向 vs 反向 =====
        gap = ev.v - bucket.valence
        contrast = abs(gap)

        if contrast < r['contrast_gate']:
            # 同向证据：不是反驳，是**再次固化**。这正是reinforcement。
            bucket.activation_count += 1
            bucket.last_active_t = t_now
            # 渐近饱和，永远不会真正顶到 10：
            # 每次强化的增量随当前重要度递减，越重要的记忆越难被进一步抬高
            bucket.importance += 0.18 * (9.8 - bucket.importance) / 9.8
            out['action'] = 'reinforce'
            out['detail'] = f'同向证据 gap={gap:+.3f} → 再次固化'
            return out

        # ===== 反向：累积反证据 =====
        imp = bucket.impedance(t_now)
        gain = r['evidence_gain'] * ev.salience * contrast
        bucket.counter_evidence += gain
        threshold = evidence_threshold(imp)

        out['evidence'] = round(bucket.counter_evidence, 4)
        out['threshold'] = round(threshold, 4)
        out['impedance'] = round(imp, 4)

        if bucket.counter_evidence < threshold:
            out['action'] = 'accumulating'
            out['detail'] = (f'反证据累积 {bucket.counter_evidence:.3f}/'
                             f'{threshold:.3f} (阻抗 {imp:.2f})')
            return out

        return self._try_update(bucket, ev, t_now, imp, threshold, out, direct=True)

    # --------------------------------------------------------
    def offer_cross(self, bucket, ev, t_now, weight):
        """
        跨主题间接反证。

        为什么必须有这条：主题是按刺激类别分的，进同一个桶的事件天生同向，
        桶内永远攒不出反证据 —— 主题层会变成死的、永远不动的一坨。
        但现实中改写"他在工作中伤害我"这个看法的，恰恰是他**在工作中**的善意。
        所以反证据必须能横向流动：同客体、同场景、不同类别的经历，
        以较低的权重互相软化。
        """
        r = C.RECONSOLIDATION
        out = {'t': t_now, 'bucket': bucket.id, 'action': 'none', 'detail': ''}

        if t_now - bucket.created_t < r['min_age_hours']:
            return out
        if ev.salience < r['min_salience']:
            return out
        if bucket.last_recon_t is not None and \
                t_now - bucket.last_recon_t < r['cooldown_hours']:
            return out

        gap = ev.v - bucket.valence
        contrast = abs(gap)
        # 不反向就不算反证据；也不做同向加固，避免和 offer() 重复计数
        if contrast < r['contrast_gate']:
            return out

        imp = bucket.impedance(t_now)
        bucket.counter_evidence += (r['evidence_gain'] * ev.salience
                                    * contrast * weight)
        threshold = evidence_threshold(imp)

        out['evidence'] = round(bucket.counter_evidence, 4)
        out['threshold'] = round(threshold, 4)
        out['impedance'] = round(imp, 4)

        if bucket.counter_evidence < threshold:
            out['action'] = 'cross_accumulating'
            out['detail'] = (f'间接反证 {bucket.counter_evidence:.3f}/'
                             f'{threshold:.3f} (阻抗 {imp:.2f})')
            return out

        return self._try_update(bucket, ev, t_now, imp, threshold, out, direct=False)

    # --------------------------------------------------------
    def _try_update(self, bucket, ev, t_now, imp, threshold, out, direct=True):
        """开窗并做有限幅度的改写。offer 与 offer_cross 共用。"""
        r = C.RECONSOLIDATION
        gap = ev.v - bucket.valence
        old_v, old_a = bucket.valence, bucket.arousal

        # 模式（主题层）的改写更慢，见 THEME['update_rate_scale']
        shift = min(r['max_shift_per_window'],
                    r['update_rate_base'] * abs(gap) * bucket.update_scale)
        if r['impedance_damping']:
            shift = shift / (1.0 + imp)
        shift = clamp(shift, 0.0, r['max_shift_per_window'])
        # k = 本次实际移动比例；valence 的绝对位移受 max_shift 限制，
        # 唤醒按同一个比例向新证据移动 —— 这样"去激化"才会真的发生，
        # 而不是只在效价上改写、情绪强度却原封不动。
        k = clamp(shift / max(abs(gap), C.EPS), 0.0, 1.0)
        new_v = clamp(old_v + gap * k, 0.0, 1.0)
        new_a = clamp(old_a + (ev.a - old_a) * k, 0.0, 1.0)

        bucket.valence = new_v
        bucket.arousal = new_a
        bucket.counter_evidence = 0.0
        bucket.last_recon_t = t_now
        bucket.last_active_t = t_now

        signed = round(new_v - old_v, 4)

        # ===== 留痕（版本链，原始值永不删除）=====
        if len(bucket.appraisal_chain) < C.RECONSOLIDATION['max_appraisal_versions']:
            bucket.appraisal_chain.append({
                't': t_now, 'day': round(t_now / 24.0, 2),
                'from_v': round(old_v, 4), 'to_v': round(new_v, 4),
                'from_a': round(old_a, 4), 'to_a': round(new_a, 4),
                'trigger_event': ev.id, 'trigger_stim': ev.stim,
                'shift': signed, 'impedance': round(imp, 4),
                'evidence_thrown': round(threshold, 4),
                'direct': direct,
            })

        kind = '重评' if direct else '间接重评'
        # 前端的竖线标记与版本链都要按天定位，day 必须在这里就带上
        out['day'] = round(t_now / 24.0, 2)
        out['action'] = 'reconsolidated'
        out['detail'] = (f'{kind} v {old_v:.3f} → {new_v:.3f} / '
                         f'a {old_a:.3f} → {new_a:.3f} '
                         f'(Δ{signed:+.4f}, 阻抗 {imp:.2f})')
        self.log.append(out)
        return out
