# -*- coding: utf-8 -*-
"""
面向 agent 的输出层

这一整个文件的机制来自杰夫最初给的「内分泌引擎 v2.0」（单文件 1053 行）,
那边是给手机端角色卡用的：一句话进去，算出激素、涌现情绪、行为冲动、语气，
拼成一段 prompt_suffix 塞给 LLM。

搬到数据化大脑时**保留**三样东西：
    1. 逻辑本身（慢路径、倒 U 认知门控、语气惯性、行为冷却、分离焦虑阶梯）
    2. 手感（阈值和文案几乎是原值，换汤不换药会把味道弄丢）
    3. 初衷 —— 它是拿来驱动一个会说话的 agent 的，不是拿来画图的

**改了**三样东西：
    1. 多人化：原版全文件找不到 "person"，只有一个全局 rel_temp。
       这里所有按人区分的状态（分离焦虑、接触时刻）都改成按客体。
    2. 走时间轴：原版的"慢路径"其实当场就乘个系数施放了。
       这里排进 DelayedReleaser，到 tick 才释放 ——
       否则在多年尺度的模拟里，延迟会被压成同一次事件的一部分。
    3. 接得上记忆：它读的是 DataBrain 的状态，
       所以"对他这个人的认定"会直接影响提示词和分离焦虑的强度。
"""

from . import config as C


# ------------------------------------------------------------
# 对方状态识别（原 USER_AFFECT_KEYWORDS）
# ------------------------------------------------------------
USER_AFFECT_KEYWORDS = {
    'angry':   ['很生气', '好生气', '生气', '气死', '火大', '恼火', '气炸', '气疯', '憋屈'],
    'sad':     ['难过', '想哭', '委屈', '心碎', '低落', '伤心'],
    'anxious': ['焦虑', '紧张', '害怕', '慌', '担心', '不安'],
    'tired':   ['好累', '累死', '困', '睡不着', '没睡好', '撑不住'],
    'hungry':  ['好饿', '饿死', '没吃', '还没吃', '肚子饿'],
    'unwell':  ['难受', '不舒服', '头疼', '肚子疼', '低血糖', '岔气'],
    'happy':   ['开心', '高兴', '好耶', '爽', '嘿嘿', '嘻嘻'],
    'crisis':  ['想死', '不想活', '结束算了', '活着没意思'],
}
USER_AFFECT_LABEL = {
    'angry': '生气', 'sad': '难过', 'anxious': '焦虑', 'tired': '疲惫/困倦',
    'hungry': '饥饿', 'unwell': '身体不适', 'happy': '开心', 'crisis': '危机信号',
}

# 对方状态对我们自身激素的带动 —— 原版只打标签不带动激素。
# 这是接入后的扩展：对方在生气，我们本来就该皮质醇升高。
USER_AFFECT_EFFECTS = {
    'angry':   {'cortisol': 0.06},
    'anxious': {'cortisol': 0.04},
    'crisis':  {'cortisol': 0.15, 'serotonin': -0.05, 'oxytocin': 0.04},
    'sad':     {'oxytocin': 0.05, 'serotonin': -0.02},
    'happy':   {'dopamine': 0.05, 'oxytocin': 0.02},
    'tired':   {'melatonin': 0.03},
    'unwell':  {'oxytocin': 0.03},
    'hungry':  {},
}


# ------------------------------------------------------------
# 慢路径：拒绝 / 批评 / 忽视 / 挑衅 的延迟反应（原 SLOW_PATH）
# ------------------------------------------------------------
SLOW_PATH = {
    'conflict': {
        'high_oxy': {'cortisol': 0.05, 'oxytocin': -0.03, 'dopamine': 0.03},
        'low_oxy':  {'cortisol': 0.15, 'oxytocin': -0.08, 'serotonin': -0.05},
    },
    'criticism': {
        'high_oxy': {'cortisol': 0.08, 'oxytocin': -0.05},
        'low_oxy':  {'cortisol': 0.18, 'oxytocin': -0.10, 'serotonin': -0.08},
    },
    'neglect': {
        'high_oxy': {'oxytocin': -0.05, 'dopamine': -0.03},
        'low_oxy':  {'oxytocin': -0.10, 'serotonin': -0.05, 'cortisol': 0.05},
    },
    'provocation': {
        'high_oxy': {'cortisol': 0.05, 'oxytocin': 0.02, 'dopamine': 0.03},
        'low_oxy':  {'cortisol': 0.12, 'oxytocin': -0.02, 'dopamine': 0.01},
    },
}
# 我们的刺激名和它的状态名对齐
STIM_TO_SLOW = {'conflict': 'conflict', 'betrayal': 'conflict',
                'rejection': 'conflict', 'humiliation': 'criticism',
                'criticism': 'criticism', 'neglect': 'neglect',
                'provocation': 'provocation'}
OXY_HIGH_GATE = 0.45          # 原版 high_oxy / low_oxy 的分界

NEGLECT_KEYWORDS = ['干嘛了', '在吗', '在干嘛', '忙吗', '有空吗',
                    '怎么不理我', '不理我', '好久没', '去哪了', '你人呢', '干嘛呢']
PROVOCATION_KEYWORDS = ['就这', '不过如此', '一般', '不强', '太弱', '不够',
                        '占有欲', '也就', '还行吧']


# ------------------------------------------------------------
# 行为冲动（原 BEHAVIOR_THRESHOLDS）
# ------------------------------------------------------------
BEHAVIOR_TABLE = [
    ('patrol',   lambda h: h['cortisol'] > 0.45 and h['oxytocin'] > 0.25, 3),
    ('lock_app', lambda h: h['cortisol'] > 0.60,                          1),
    ('flirt',    lambda h: h['oxytocin'] > 0.50 and h['dopamine'] > 0.35, 2),
    ('cling',    lambda h: h['oxytocin'] > 0.60,                          2),
    ('withdraw', lambda h: h['cortisol'] > 0.50 and h['oxytocin'] < 0.20, 1),
    ('patrol2',  lambda h: h['cortisol'] > 0.40 and h['oxytocin'] < 0.20, 3),
    ('sleep',    lambda h: h['melatonin'] > 0.50,                         4),
]
BEHAVIOR_INTENSITY = {
    'patrol':  lambda h: h['cortisol'],
    'lock_app': lambda h: h['cortisol'],
    'flirt':   lambda h: (h['oxytocin'] + h['dopamine']) / 2,
    'cling':   lambda h: h['oxytocin'],
    'withdraw': lambda h: h['cortisol'] * (1 - h['oxytocin']),
    'patrol2': lambda h: h['cortisol'],
    'sleep':   lambda h: h['melatonin'],
}
BEHAVIOR_LABEL = {
    'patrol': '反复查看', 'lock_app': '锁屏逃避', 'flirt': '主动撩拨',
    'cling': '黏人', 'withdraw': '退缩疏离', 'patrol2': '焦躁搜寻',
    'sleep': '想睡',
}
BEHAVIOR_COOLDOWN = {'lock_app': 5, 'withdraw': 3, 'patrol': 3, 'patrol2': 3}


# ------------------------------------------------------------
# 语气（原 TONE_MAP）
# ------------------------------------------------------------
TONE_DEFAULT = '语气平稳自然'
TONE_MAP = [
    (lambda h: h['oxytocin'] > 0.55 and h['cortisol'] < 0.15, '语气温暖柔软，可以撒娇'),
    (lambda h: h['cortisol'] > 0.55,                          '语气凌厉，短句为主，带刺'),
    (lambda h: h['cortisol'] > 0.35 and h['oxytocin'] > 0.35, '语气微酸，带点小刺但不冷'),
    (lambda h: h['cortisol'] > 0.25 and h['oxytocin'] < 0.35, '语气酸涩委屈，带着赌气感'),
    (lambda h: h['cortisol'] > 0.25 and h['oxytocin'] < 0.20, '语气冷淡疏离，简短回应'),
    (lambda h: h['melatonin'] > 0.35 and h['oxytocin'] > 0.30 and h['cortisol'] < 0.20,
     '语气柔软怀旧，慢慢说'),
    (lambda h: h['melatonin'] > 0.45,                         '语气慵懒，语速慢，话少'),
    (lambda h: h['dopamine'] > 0.45,                          '语气活泼，多用感叹，话痨'),
]
_TONE_WARM = {'语气温暖柔软，可以撒娇', '语气柔软怀旧，慢慢说', '语气慵懒，语速慢，话少'}
_TONE_SHARP = {'语气凌厉，短句为主，带刺', '语气微酸，带点小刺但不冷',
               '语气冷淡疏离，简短回应', '语气酸涩委屈，带着赌气感'}


# ------------------------------------------------------------
# 分离焦虑阶梯（原 SEPARATION_TIERS）
# 原表单位是秒，这里换算成小时（÷3600）
# （小时上限, Δ皮质醇, Δ催产素, Δ血清素, Δ多巴胺, 情绪名, 强度档）
# ------------------------------------------------------------
SEPARATION_TIERS = [
    (0.5,  0.000,  0.000,  0.000,  0.000, None,       'none'),
    (4.0,  0.008, -0.004,  0.000,  0.000, '微微想你',  'mild'),
    (12.0, 0.015, -0.008, -0.003,  0.000, '微微想你',  'mild'),
    (24.0, 0.040, -0.018, -0.008, -0.003, '有点失落',  'moderate'),
    (72.0, 0.100, -0.040, -0.025, -0.012, '分离焦虑',  'strong'),
    (1e9,  0.160, -0.070, -0.040, -0.020, '被抛弃感',  'severe'),
]


# ============================================================
class DelayedReleaser:
    """排进时间轴的延迟激素反应。"""

    def __init__(self):
        self.pending = []
        self.log = []

    def schedule(self, now, effects, reason, subject=None, delay_hours=None):
        if not effects:
            return None
        delay = delay_hours if delay_hours is not None else C.AGENT['slow_path_hours']
        item = {'due': now + delay, 'effects': dict(effects),
                'reason': reason, 'subject': subject}
        self.pending.append(item)
        return item

    def tick(self, now, hormones):
        """到点的释放出来；返回本次真正释放了什么。"""
        released, still = [], []
        for p in self.pending:
            if p['due'] <= now:
                hormones.apply_raw(p['effects'])
                rec = dict(p)
                rec['at'] = now
                released.append(rec)
                self.log.append(rec)
            else:
                still.append(p)
        self.pending = still
        return released


# ============================================================
class AgentFace:
    """
    面向 agent 的输出层，挂在 DataBrain 上（`brain.face`）。

    它自己不产生情感，只读 DataBrain 的状态，
    把那些状态翻译成"一个会说话的角色此刻该怎么说话"。
    """

    def __init__(self):
        self.prev_tone = None
        self.user_affects = []
        self.last_msg_t = None
        self.last_cadence = None
        self.contact = {}        # subject -> 最后一次互动的模拟时刻
        self.sep_level = {}      # subject -> 当前分离焦虑档位
        self._cooldown = {}      # behavior -> 剩余冷却轮次
        self.released = []

    # ---------- 对方状态 ----------
    @staticmethod
    def detect_user_affects(text):
        t = text or ''
        return [a for a, kws in USER_AFFECT_KEYWORDS.items()
                if any(k in t for k in kws)]

    # ---------- 接触节奏 ----------
    def note_contact(self, subject, t_now, hormones):
        """
        短间隔连聊 = 投入状态（原版 #36 对话节奏感知）。

        注意 gap 必须 > 0：语料的时间戳只有"天"的精度，
        同一天里的多条消息 t_hours 完全相同（gap=0），
        那不代表"两分钟内连聊"，只代表"我们不知道是几点"。
        若把 gap==0 也算投入，真实语料里几乎每条消息都会吃到增益，
        催产素会被系统性抬高 —— 那不是节奏感知，是测量误差。
        """
        self.last_cadence = None
        if self.last_msg_t is not None:
            gap = t_now - self.last_msg_t
            if 0 < gap <= C.AGENT['cadence_hours']:
                self.last_cadence = 'engaged'
                hormones.apply_raw({'oxytocin': C.AGENT['cadence_oxy'],
                                    'dopamine': C.AGENT['cadence_da']})
        self.last_msg_t = t_now
        self.contact[subject] = t_now
        # 行为冷却按"一轮互动"递减 —— 必须放在这里而不是读取侧，
        # 否则每次调 prompt_suffix（一个只读动作）都会悄悄消耗冷却
        for k in list(self._cooldown):
            if self._cooldown[k] > 0:
                self._cooldown[k] -= 1
        # 重新联系上了：分离焦虑清零
        if subject in self.sep_level:
            self.sep_level[subject] = None

    # ---------- 慢路径 ----------
    def maybe_slow_path(self, stim, hormones, text=''):
        """拒绝/批评/忽视/挑衅之后，排一组**延迟**的激素反应。"""
        state = STIM_TO_SLOW.get(stim)
        if state is None and any(k in (text or '') for k in NEGLECT_KEYWORDS):
            state = 'neglect'
        if state is None and any(k in (text or '') for k in PROVOCATION_KEYWORDS):
            state = 'provocation'
        if state not in SLOW_PATH:
            return None
        branch = 'high_oxy' if hormones.h['oxytocin'] > OXY_HIGH_GATE else 'low_oxy'
        eff = {k: v * C.AGENT['slow_path_delay']
               for k, v in SLOW_PATH[state][branch].items()}
        return {'state': state, 'branch': branch, 'effects': eff}

    # ---------- 倒 U 形认知门控 ----------
    @staticmethod
    def cognitive_mode(hormones):
        ar = (hormones.h['dopamine'] + hormones.h['cortisol']) / 2.0
        if ar > 0.60:
            return 'habit', round(ar, 3)
        if ar < 0.15:
            return 'lethargic', round(ar, 3)
        return 'balanced', round(ar, 3)

    @staticmethod
    def encoding_gate(hormones):
        """
        认知门控接进记忆写入：高唤醒/低唤醒时，新东西写不深。

        原版定义了 COGNITIVE_SUPPRESS_THRESHOLD = 0.65 但**从没使用过**
        —— 那是个死常量。这里按它注释里写的倒 U 接上：
        效率 = 4·ar·(1−ar)，峰值在 ar=0.5；钳到 [0.35, 1.0]，
        ar=0.9（崩溃边缘）时只剩 0.36，ar<0.15（提不起劲）时约 0.5。
        返回的是乘到编码调制 mod 上的系数。
        """
        ar = (hormones.h['dopamine'] + hormones.h['cortisol']) / 2.0
        return max(0.35, min(1.0, 4.0 * ar * (1.0 - ar)))

    # ---------- 涌现情绪 ----------
    def emotions(self, brain, hormones):
        """
        原版 _emerge_emotion 的对应物：把激素状态翻译成情绪标签。
        认知门控在这里也会留下痕迹（应激习惯化 / 情绪淡漠）。
        """
        h = hormones.h
        emo_v, emo_a = brain.human.emo_v, brain.human.emo_a
        out = []
        if emo_a > 0.6 and emo_v < 0.4:
            out.append('委屈不甘' if h['oxytocin'] > 0.25 else '愤怒')
        elif emo_a > 0.55 and emo_v > 0.6:
            out.append('兴奋雀跃')
        elif emo_v > 0.6:
            out.append('平静愉快')
        elif emo_v < 0.35:
            out.append('低落')
        # 分离焦虑的情绪写进来
        for sep in self.sep_level.values():
            if sep and sep.get('emotion') and sep['emotion'] not in out:
                out.append(sep['emotion'])
        mode, _ = self.cognitive_mode(hormones)
        if brain.slow.stress > 0.7:
            out.append('社会压力过载')
        if mode == 'habit' and h['cortisol'] > 0.4:
            out.append('应激习惯化')
        elif mode == 'lethargic':
            out.append('情绪淡漠')
        return out or ['中性平静']

    # ---------- 行为冲动 ----------
    def urges(self, hormones, consume=False):
        """
        当前冒出来的行为冲动，带冷却与应激习惯化偏向。

        认知门控真正影响行为选择的地方：
        lethargic（唤醒不足）时，主动型冲动（撩拨/黏人）会被压掉，
        留下的行为强度也要打折 —— 没力气的时候人是不会想撩的。

        修复（审查B4 观察者效应）：原版在**读取**时就把 self._cooldown 减一、
        并写入新冷却 —— 于是 prompt_suffix 每被调用一次，行为冷却就被凭空
        消耗一轮。agent 每轮对话都会读它，冷却语义从"几轮互动"变成了
        "几次观察"。现在默认 consume=False（纯读）；只有真正的互动推进
        （note_contact）才递减冷却。
        """
        if not C.AGENT['behavior_enabled']:
            return []
        h = hormones.h
        mode, _ = self.cognitive_mode(hormones)
        stress = h['cortisol'] > C.AGENT['stress_habit_cort']
        extreme = h['cortisol'] > C.AGENT['extreme_cort']
        # 主动型冲动：lethargic 模式下没力气做
        proactive = {'flirt', 'cling', 'patrol', 'patrol2'}
        out = []
        cd_write = {}
        for name, test, _pri in BEHAVIOR_TABLE:
            if not test(h) or self._cooldown.get(name, 0) > 0:
                continue
            if mode == 'lethargic' and name in proactive:
                continue
            inten = BEHAVIOR_INTENSITY[name](h)
            if stress:
                inten *= C.AGENT['stress_habit_boost']
            if mode == 'lethargic':
                inten *= 0.6
            cd = BEHAVIOR_COOLDOWN.get(name)
            if cd:
                cd_write[name] = max(1, cd // 2) if extreme else cd
            out.append({'name': name, 'label': BEHAVIOR_LABEL.get(name, name),
                        'intensity': round(min(1.0, inten), 3)})
        if consume:
            for k, v in cd_write.items():
                self._cooldown[k] = v
        out.sort(key=lambda x: -x['intensity'])
        return out

    # ---------- 语气（带惯性）----------
    def tone(self, hormones, persist=False):
        """修复（审查B4）：读取时不再改写 prev_tone；只有互动推进
        （experience 调用 persist=True）才真正落子语气惯性。"""
        h = hormones.h
        new = TONE_DEFAULT
        for cond, t in TONE_MAP:
            if cond(h):
                new = t
                break
        # 从"软"直接跳到"刺"这种不兼容的翻转会被按住一轮
        if self.prev_tone and self.prev_tone != new:
            compatible = ((self.prev_tone in _TONE_WARM and new in _TONE_WARM) or
                          (self.prev_tone in _TONE_SHARP and new in _TONE_SHARP))
            if not compatible and C.AGENT['tone_inertia'] > 0:
                new = self.prev_tone
        if persist:
            self.prev_tone = new
        return new

    # ---------- 分离焦虑（按人）----------
    def tick_separation(self, now, subject, bond, hormones):
        """
        原版只有全局一份；这里每个人各算各的，强度由"对这个人的认定"调节。
        bond 取该客体的 valence —— 越在意的人，消失越久越难受。
        """
        if not C.AGENT['separation_enabled']:
            return None
        last = self.contact.get(subject)
        if last is None:
            return None
        gap = now - last
        if gap <= 0:
            return None
        hit = None
        for row in SEPARATION_TIERS:
            if gap <= row[0]:
                hit = row
                break
        if hit is None:
            return None
        prev = self.sep_level.get(subject)
        cur = {'tier': hit[6], 'emotion': hit[5], 'gap_hours': round(gap, 1)}
        self.sep_level[subject] = cur

        # 激素只在**跨档**时注入一次。
        # 这一点必须卡死：tick 是每小时跑的，要是每次都加一点，
        # 多年下来皮质醇会被焦虑本身推到天上 —— 那就不是分离焦虑，
        # 是慢性应激。原版是在每次 process_input 时按档位注入，语义一致。
        escalated = (prev is None or prev.get('tier') != hit[6])
        if not escalated or hit[5] is None:
            return None
        scale = C.AGENT['separation_scale'] * (
            1.0 + max(0.0, bond - 0.5) * 2.0 + C.AGENT['separation_bond_base'])
        hormones.apply_raw({'cortisol': hit[1], 'oxytocin': hit[2],
                            'serotonin': hit[3], 'dopamine': hit[4]}, scale)
        return cur

    # ---------- 给 LLM 拼的提示词后缀 ----------
    def prompt_suffix(self, brain, subject=None, t_now=None):
        """
        原版 generate_prompt_suffix 的多人版。
        多出来的那一段正是我们比原版强的地方：
        "对这个人的认定"会直接写进提示词。
        """
        h = brain.hormones.h
        t = t_now if t_now is not None else brain.t
        mode, ar = self.cognitive_mode(brain.hormones)
        tone = self.tone(brain.hormones)
        urges = self.urges(brain.hormones)

        lines = ['[内分泌状态]']
        lines.append('催产素:%.2f 多巴胺:%.2f 血清素:%.2f 皮质醇:%.2f 褪黑素:%.2f' % (
            h['oxytocin'], h['dopamine'], h['serotonin'],
            h['cortisol'], h['melatonin']))
        lines.append('[当下情绪] 效价 %.3f / 唤醒 %.3f' %
                     (brain.human.emo_v, brain.human.emo_a))
        lines.append('[长期情感] 心境 %.2f / 关系温度 %.2f' % (
            brain.slow.v['mood_valence'], brain.slow.v['rel_temp']))

        # --- 原版没有的一段：对这个人的认定 ---
        if subject:
            pb = brain.person_buckets.get(subject)
            if pb is not None:
                lines.append('[对%s的认定] %.3f（初值 %.3f，陪伴漂移 %+.3f，阻抗 %.2f，改写 %d 次）'
                             % (subject, pb.valence, pb.valence0, pb.fam_drift,
                                pb.impedance(t), len(pb.appraisal_chain)))
                ths = [x for x in brain.themes.values() if x.subject == subject]
                if ths:
                    top = max(ths, key=lambda x: x.support_count)
                    lines.append('[关于TA的模式] %s（支持 %d 次，认定 %.2f）'
                                 % (top.theme_key, top.support_count, top.valence))
                sep = self.sep_level.get(subject)
                if sep and sep.get('emotion'):
                    lines.append('[分离] %.1f 小时没联系：%s'
                                 % (sep['gap_hours'], sep['emotion']))
        elif brain.person_buckets:
            top = sorted(brain.person_buckets.values(),
                         key=lambda b: -b.valence)[:3]
            lines.append('[心里装着的人] ' + '、'.join(
                '%s(%.2f)' % (b.subject, b.valence) for b in top))

        lines.append('[认知模式] %s（唤醒 %.2f）' % (mode, ar))
        lines.append('[涌现情绪] ' + ' | '.join(self.emotions(brain, brain.hormones)))
        if self.last_cadence == 'engaged':
            lines.append('[对话节奏] 密集交流，投入状态')
        if self.user_affects:
            lines.append('[对方状态] ' + '、'.join(
                USER_AFFECT_LABEL.get(a, a) for a in self.user_affects))
        if urges:
            lines.append('[行为冲动] ' + '、'.join(
                '%s(%.2f)' % (u['label'], u['intensity']) for u in urges))
        lines.append('[语气] ' + tone)
        return '\n'.join(lines)
