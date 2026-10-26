# -*- coding: utf-8 -*-
"""
人味层（Human Layer）

为什么需要这一层：
    前面几轮把"记忆怎么形成、怎么被改写"做扎实了，但那套东西跑起来
    **太连贯、太精确、太被动** —— 而这恰恰是最不像人的地方。

    真人长期相处时表现出的六件事，当前模型全都没有：

    1. FAB          负性记忆的情感衰减比正性快（所以人回忆过去偏玫瑰色）
                    没有它 → 显得记仇、永不宽恕
    2. 习惯化        重复的赞美/抱怨会越来越不管用
                    没有它 → 第一百次夸它和第一次一样开心，很假
    3. 情绪惯性+不应期 刚大悲过不会立刻因小事再大悲
                    没有它 → 情绪瞬变，显得躁
    4. 自发回忆      没事的时候也会想起某人，并因此改变当下的心情
                    没有它 → 永远被动响应，像工具不像人
    5. 矛盾情感      对同一个人可以同时又爱又恨，态度会摇摆
                    没有它 → 态度单一确定，显假
    6. Zeigarnik    没完成的事记得更牢
                    没有它 → 少了"惦记着某件事"这种很人的状态

    每一条都有文献支撑，见各方法注释。
"""

import math
import random
from . import config as C
from .state import clamp, decay_factor


class HumanLayer:
    def __init__(self, seed=0):
        self.rng = random.Random(seed + 99991)
        cfg = C.HUMAN

        # --- 1. 习惯化 ---
        self.habit = {}                  # stim -> 加权计数
        self.habit_last = {}             # stim -> 上次出现时间

        # --- 3. 情绪惯性 ---
        self.emo_v = 0.5
        self.emo_a = 0.25
        self.last_strong_t = None
        self.emo_trace = []              # 供社会化测试分析

        # --- 4. 自发回忆 ---
        self.spontaneous_log = []

        self.enabled = {k: cfg[k].get('enabled', True) for k in cfg}

    # =========================================================
    # 1. FAB：负性情感衰减更快
    # =========================================================
    def tick_fab(self, bucket, dt_hours):
        """
        Fading Affect Bias（Walker, Vogl & Thompson 1997；Ritchie et al. 复核）：
        负性事件回忆时的情感强度衰减**快于**正性事件。

        修复（审查 C4）：原实现把效价每天往 0.52 拉 —— 多年尺度上等于
        指数收敛到 0.52，实测过半情节记忆被"自动美化"，且不进版本链、
        不算 revisions，违反本项目自己的宪法"情节层不改写，只衰减"。
        FAB 的原始含义是**情感强度**消退，不是事实评价被改写。
        现在默认只衰减唤醒；想要旧行为做消融，把 fab['valence_drift'] 设 True。
        （fab_decay 指标改测"负性记忆的唤醒消退"，语义与文献一致。）
        """
        if not self.enabled.get('fab'):
            return
        # 客体层不参与：对人的判断应当由新的经历改写，而不是被时间自动美化
        if getattr(bucket, 'is_person', False):
            return
        fab = C.HUMAN['fab']
        if fab.get('valence_drift', False):      # 旧行为开关（消融实验用）
            dt_days = dt_hours / 24.0
            if bucket.valence < fab['target']:
                bucket.valence += (fab['target'] - bucket.valence) * fab['rate'] * dt_days
            bucket.valence = clamp(bucket.valence, 0.0, 1.0)
        # 唤醒随时间自然回落（情绪强度会淡，事实不会）
        # 地板压到 0.02：旧值 0.10 在长程模拟里把消退钳死（fab_decay≈0 是钳制假象）
        bucket.arousal *= decay_factor(dt_hours, fab['arousal_half_life_days'] * 24.0)
        bucket.arousal = max(fab['arousal_floor'], bucket.arousal)
        bucket.arousal = clamp(bucket.arousal, 0.0, 1.0)

    # =========================================================
    # 2. 习惯化：重复刺激越来越不管用
    # =========================================================
    def note_stim(self, stim, t_now, subject=None):
        if not self.enabled.get('habituation'):
            return 1.0
        h = C.HUMAN['habituation']
        # 修复（审查B8）：习惯化按 (刺激, 客体) 分账 ——
        # 被一个人磨出茧子不该传染给所有人（原版全局计数：莉莉被夸30次后
        # praise 因子 0.35，汤姆随后的夸奖跟着一起钝化）。
        key = (stim, subject or '_')
        # 距离上次出现越久，习惯化越消退（久别重逢不会无感）
        cnt = self.habit.get(key, 0.0)
        last = self.habit_last.get(key)
        if last is not None:
            gap = max(0.0, t_now - last)
            cnt *= decay_factor(gap, h['decay_half_life'])
        self.habit[key] = cnt + 1.0
        self.habit_last[key] = t_now
        return self.habituation_factor(stim, subject)

    def habituation_factor(self, stim, subject=None):
        if not self.enabled.get('habituation'):
            return 1.0
        h = C.HUMAN['habituation']
        key = (stim, subject or '_')
        # 兼容 social.py 的钝化统计：聚合该刺激在所有人身上的最大计数
        if subject is None:
            cnts = [c for (s, _), c in self.habit.items() if s == stim]
            if not cnts:
                return 1.0
            cnt = max(cnts)
        else:
            cnt = self.habit.get(key, 0.0)
        return max(h['floor'], math.exp(-cnt / h['scale']))

    # =========================================================
    # 3. 情绪惯性 + 不应期
    # =========================================================
    def push_emotion(self, v, a, t_now, record=True):
        """
        情绪不会瞬间切换。刚大悲过的人不会因为一件小事立刻又大悲。
        k = 本次事件能把当前情绪拉动多少；处于不应期时 k 被压低。
        """
        cfg = C.HUMAN['inertia']
        if not self.enabled.get('inertia'):
            self.emo_v, self.emo_a = v, a
            self._record(t_now, v, a, record)
            return self.emo_v, self.emo_a

        k = cfg['k_base']
        if self.last_strong_t is not None:
            since = t_now - self.last_strong_t
            if since < cfg['refractory_hours']:
                # 越接近上次强情绪，越难被再次激起
                depth = 1.0 - since / cfg['refractory_hours']
                k *= (1.0 - cfg['refractory_depth'] * depth)
        k = clamp(k, 0.05, 1.0)

        self.emo_v += (v - self.emo_v) * k
        self.emo_a += (a - self.emo_a) * k
        if a >= cfg['strong_a']:
            self.last_strong_t = t_now
        self.emo_v = clamp(self.emo_v, 0.0, 1.0)
        self.emo_a = clamp(self.emo_a, 0.0, 1.0)
        self._record(t_now, self.emo_v, self.emo_a, record)
        return self.emo_v, self.emo_a

    def tick_emotion(self, t_now, dt_hours, rest_v=0.5, rest_a=0.25):
        """没有事件时，情绪向（有起伏的）静息水位回落。"""
        cfg = C.HUMAN['inertia']
        rv, ra = self.resting_point(t_now, rest_v, rest_a)
        f = 1.0 - decay_factor(dt_hours, cfg['rest_half_life'])
        self.emo_v += (rv - self.emo_v) * f
        self.emo_a += (ra - self.emo_a) * f
        self.emo_v = clamp(self.emo_v, 0.0, 1.0)
        self.emo_a = clamp(self.emo_a, 0.0, 1.0)
        self._record(t_now, self.emo_v, self.emo_a, False)

    def _record(self, t, v, a, on_event=True):
        self.emo_trace.append({'t': t, 'day': t / 24.0, 'v': v, 'a': a,
                               'event': on_event})

    # =========================================================
    # 4. 自发回忆：没事的时候也会想起
    # =========================================================
    def _poisson(self, lam):
        """泊松采样。原来用"每天必定想起一次"的伯努利，结果频率恰好 1.0——
        每天都想一次本身就是不自然的规律。真人是：有时连着想起好几次，
        有时好几天都不想起。"""
        if lam <= 0:
            return 0
        if lam > 30:
            return int(lam)
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while k < 100:
            k += 1
            p *= self.rng.random()
            if p <= L:
                break
        return k - 1

    def maybe_spontaneous_recall(self, buckets, t_now, dt_hours):
        """
        人在没有外部刺激的静默期也会自发想起某人，而且想起的那个人
        会真实地改变此刻的心情。这是"被惦记"的动力学来源。

        选谁被想起：分数高的优先，且与当前心境一致的更容易被想起
        （情绪一致性提取 mood-congruent retrieval）。
        """
        cfg = C.HUMAN['spontaneous']
        if not self.enabled.get('spontaneous') or not buckets:
            return []
        n = self._poisson(cfg['base_rate_per_day'] * (dt_hours / 24.0))
        picks = []
        for _ in range(n):
            got = self._recall_one(buckets, t_now)
            if got is not None:
                picks.append(got)
        return picks

    def _recall_one(self, buckets, t_now):
        cfg = C.HUMAN['spontaneous']
        cands = [b for b in buckets if not b.archived]
        if not cands:
            return None
        weights = []
        for b in cands:
            w = max(0.05, b.score(t_now))
            # 情绪一致性：心情好时更容易想起好事
            congr = math.exp(-abs(b.valence - self.emo_v) / cfg['congruence_width'])
            weights.append(w * (1.0 - cfg['mood_congruence'] + cfg['mood_congruence'] * congr))
        tot = sum(weights)
        if tot <= 0:
            return None
        r = self.rng.random() * tot
        acc = 0.0
        picked = cands[-1]
        for b, w in zip(cands, weights):
            acc += w
            if r <= acc:
                picked = b
                break

        picked.activation_count += 1
        picked.last_active_t = t_now
        # 想起来的事会真实地扰动此刻的心情
        self.emo_v += (picked.valence - self.emo_v) * cfg['emotion_pull']
        self.emo_a += (picked.arousal - self.emo_a) * cfg['emotion_pull'] * 0.6
        self.spontaneous_log.append({'day': round(t_now / 24.0, 2),
                                     'bucket': picked.id,
                                     'valence': round(picked.valence, 3)})
        return picked

    # =========================================================
    # 3b. 静息水位的自然起伏
    # =========================================================
    def resting_point(self, t_now, base_v, base_a):
        """
        人的静息心情不是一条直线。即使什么都没发生，每天也会有轻微起伏
        （昼夜、周节律、说不清的缘由）。
        没有这个，模型在平淡期会变成一条完全水平的线 —— 那也不像人，
        那像关掉了。
        """
        cfg = C.HUMAN['inertia']
        wob = cfg['rest_wobble'] * math.sin(
            2 * math.pi * t_now / (cfg['rest_wobble_days'] * 24.0))
        # 叠加一个更慢的周节律
        wob += cfg['rest_wobble'] * 0.5 * math.sin(
            2 * math.pi * t_now / (cfg['rest_wobble_days'] * 24.0 * 3.3) + 1.1)
        return clamp(base_v + wob, 0.0, 1.0), clamp(base_a + wob * 0.4, 0.0, 1.0)

    # =========================================================
    # 5. 矛盾情感：对一个人可以同时又爱又恨
    # =========================================================
    @staticmethod
    def ambivalence(bucket):
        """
        同时存在正负证据的程度。0=纯粹，1=极度矛盾。
        矛盾度高的人，你对他的态度会摇摆 —— 这是真人，
        一个 valence 恒定不变的人反而假。
        """
        pos = bucket.pos_count
        neg = bucket.neg_count
        tot = pos + neg
        if tot == 0:
            return 0.0
        return min(pos, neg) / tot * 2.0



# ============================================================
# 6. Zeigarnik：没完成的事记得更牢
# ============================================================
# 哪些刺激会留下"未完成的环路"
OPEN_LOOP_STIMS = {'apology', 'criticism', 'conflict', 'crisis',
                   'rejection', 'neglect', 'humiliation', 'betrayal', 'loss'}


def is_open_loop(stim):
    return stim in OPEN_LOOP_STIMS
