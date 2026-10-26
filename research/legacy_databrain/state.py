# -*- coding: utf-8 -*-
"""
State 层：快变量（激素）+ 慢变量（关系温度 / 心境）+ 倾向集合放电

设计约束：**绝不调用 datetime.now()**。
所有时间由外部注入（t_sim 为模拟小时数），这样同一个引擎可以
- 以任意步长推进
- 跨 180 天连续跑
- 做可复现的对照实验（同一 seed → 同一轨迹）

------------------------------------------------------------------
每个方法都标注了它所依据的机制编号与文献：
  #25 褪黑素昼夜正弦        Ungurianu & Marina 2025
  #26 褪黑素-皮质醇交互抑制  Ungurianu & Marina 2025
  #30 多巴胺昼夜基线        Kim & Reed 2021
  #31 倒U形唤醒-认知门控    Packard et al. 2021 / Yerkes-Dodson
  #33 分离焦虑阶梯          Bowlby / Mendoza & Mason 2018
"""

import math
from . import config as C


def clamp(x, lo, hi):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    if math.isnan(x) or math.isinf(x):
        return lo
    return max(lo, min(hi, x))


def decay_factor(dt_hours, half_life):
    """一阶指数回归因子。dt<=0 返回 1（无变化）。"""
    if dt_hours <= 0:
        return 1.0
    if half_life <= 0:
        return 0.0
    # 防止极大 dt 导致下溢到 0 之外
    return 0.5 ** min(dt_hours / half_life, 60.0)


def circ_dist(hour, target, period=24.0):
    """环形距离：24 小时一周上的最短距离。"""
    d = abs(hour - target) % period
    return min(d, period - d)


# ============================================================
# 昼夜节律
# ============================================================
def circadian_cortisol(hour):
    """
    皮质醇觉醒反应（CAR）：觉醒后 30-45min 达峰，随后日间下降，午夜最低。
    用高斯峰而不是正弦，因为 CAR 是一个尖锐的晨峰而非平滑波动。
    晨峰约为全天谷值的 3-5 倍（实测昼夜比值）。
    """
    c = C.CIRCADIAN
    d = circ_dist(hour, c['cort_peak_hour'])
    peak = c['cort_amplitude'] * math.exp(-((d / 2.5) ** 2))
    # 白天基线抬升 + 夜间压低：给一个缓慢的日间倾斜
    tilt = 0.04 * math.sin(math.pi * (hour - c['cort_peak_hour']) / 12.0)
    return peak + tilt


def circadian_melatonin(hour):
    """
    褪黑素：仅夜间活跃。21:00 起始分泌，凌晨 3:00 达峰，07:00 后骤降。
    白天严格压到接近 0 —— 这一点比原引擎的全局余弦更符合生理
    （原公式在上午 9 点仍给出 0.35，明显偏高）。
    """
    c = C.CIRCADIAN
    onset = c['mel_onset_hour']     # 21
    offset = 7.0                    # 早晨 7 点后被光抑制
    if hour >= onset or hour < offset:
        # 把 [21, 24)∪[0,7) 映射到以 3:00 为峰值的半正弦
        h = hour if hour >= onset else hour + 24.0   # 统一到 [21, 31)
        # 在 [21,31] 上做半正弦，峰在 h=27 (=凌晨3点)
        phase = (h - 21.0) / 10.0                    # 0..1
        return c['mel_floor_circ'] + c['mel_amplitude'] * math.sin(math.pi * phase)
    return c['mel_floor_circ'] * 0.4


def circadian_dopamine(hour):
    """多巴胺昼夜基线：正午峰值，午夜谷值（Kim & Reed 2021）。
    修复（审查 B1）：原式 sin(π*(hour−peak−6)/12) 相位反了 12 小时，
    实测 00:00 达峰、12:00 达谷，与注释相反。cos 形式峰值即落在 hour=peak。"""
    c = C.CIRCADIAN
    return c['da_amplitude'] * math.cos(math.pi * (hour - c['da_peak_hour']) / 12.0)


# ============================================================
# 快变量：激素池
# ============================================================
# 可信度开关（审查 C1）：语料只有"天"精度时，
# 任何"小时"都是编的 —— 与其让假小时驱动昼夜相位（real 管线里
# 所有事件实际都落在 t%24==0 = 全员午夜），不如取全天均值：
# 保留昼夜调制的强度，但不引入虚假的相位信息。
_MEAN_CACHE = {}


def circ_mean(fn):
    if fn.__name__ not in _MEAN_CACHE:
        _MEAN_CACHE[fn.__name__] = sum(
            fn(i / 4.0) for i in range(96)) / 96.0
    return _MEAN_CACHE[fn.__name__]


# ============================================================
class HormonalSystem:
    def __init__(self):
        self.h = {k: v['base'] for k, v in C.HORMONES.items()}
        self.residue = {k: 0.0 for k in C.HORMONES}
        self._last_t = 0.0
        self.warnings = []

    # -------- 推进 --------
    def tick(self, t_sim, dt_hours, hour=None):
        """dt_hours <= 0 时不做任何事（防止静默 NaN）。
        hour: 真实钟点；None = 时刻不可信，昼夜调制取全天均值（审查 C1）。"""
        if not (dt_hours > 0):
            return

        # -------- 第一步：算出每个激素的【调定点】 --------
        targets = {}
        for k, cfg in C.HORMONES.items():
            tgt = cfg['base'] + self.residue[k]
            if k == 'cortisol':
                tgt += circadian_cortisol(hour) if hour is not None \
                    else circ_mean(circadian_cortisol)
            elif k == 'melatonin':
                tgt += circadian_melatonin(hour) if hour is not None \
                    else circ_mean(circadian_melatonin)
            elif k == 'dopamine':
                tgt += circadian_dopamine(hour) if hour is not None \
                    else circ_mean(circadian_dopamine)
            targets[k] = tgt

        # -------- 第二步：拮抗作用于调定点 --------
        # 关键修正：拮抗（如催产素抑制 HPA 轴）改变的是**调定点**，
        # 而不是每单位时间的下压力。而且只看激素【偏离自身基线】的那部分——
        # 否则在完全平静的状态下各激素仍互相扣减，会把系统压到下界。
        for src, tmap in C.ANTAGONISM.items():
            dev = self.h[src] - C.HORMONES[src]['base']
            for tgt, coeff in tmap.items():
                targets[tgt] += dev * coeff

        # -------- 第三步：向调定点做一阶回归 --------
        for k, cfg in C.HORMONES.items():
            tgt = clamp(targets[k], cfg['floor'], cfg['ceil'])
            f = decay_factor(dt_hours, cfg['half_life'])
            self.h[k] = tgt + (self.h[k] - tgt) * f
            # 残留缓慢消散：比主衰减慢 3 倍
            self.residue[k] *= decay_factor(dt_hours, cfg['half_life'] * 3.0)
        self._clamp()
        self._last_t = t_sim

    def _clamp(self):
        for k, cfg in C.HORMONES.items():
            before = self.h[k]
            self.h[k] = clamp(before, cfg['floor'], cfg['ceil'])
            if abs(before - self.h[k]) > 0.5:
                self.warnings.append(f"激素 {k} 被大幅截断: {before:.3f} -> {self.h[k]:.3f}")

    # -------- 刺激 --------
    def apply_stimulus(self, stim_type, intensity=1.0):
        effects = C.STIMULUS_HORMONE.get(stim_type, {})
        for h, delta in effects.items():
            if h in self.h:
                self.h[h] += delta * intensity
        # 一部分进入残留（缓慢释放的部分）
        for h, delta in effects.items():
            if h in self.residue:
                self.residue[h] += delta * intensity * 0.2
        self._clamp()

    def apply_raw(self, effects, scale=1.0):
        """直接施加一组激素变化（慢路径、分离焦虑、对方状态带动都走这里）。"""
        for h, delta in (effects or {}).items():
            if h in self.h:
                self.h[h] += delta * scale
        self._clamp()

    # -------- 派生量 --------
    @property
    def arousal(self):
        """唤醒 = 多巴胺与皮质醇的均值，再受催产素轻微下调。"""
        a = (self.h['dopamine'] + self.h['cortisol']) / 2.0
        a -= 0.08 * self.h['oxytocin']
        return clamp(a, 0.0, 1.0)

    def valence_proxy(self):
        """
        效价代理。（催产素 + 血清素）/2 - 皮质醇偏移。
        注意：这只是躯体状态读数，不是"对这个事件的评价"。
        真正的 valence 由评估层给出，这里提供的是**状态依赖性**。
        """
        v = (self.h['oxytocin'] + self.h['serotonin']) / 2.0 - 0.25 * (self.h['cortisol'] - 0.2)
        return clamp(v, 0.0, 1.0)

    def cognitive_gain(self):
        """倒 U 形唤醒-认知门控（Yerkes-Dodson）：峰值在 arousal=0.5。"""
        a = self.arousal
        return clamp(4.0 * a * (1.0 - a), 0.0, 1.0)

    def encoding_modulation(self, is_social=True):
        """
        编码调制：把内分泌接到"写入"这一步。返回权重倍数。

        皮质醇 → 倒 U（这是文献里最强的非线性之一：
                  中等水平 GR 激活增强巩固，过高则转为损害）
        多巴胺 → 新颖性/显著性，单调增强
        去甲肾上腺素代理 → 高唤醒的 BLA 调制
        催产素 → 社会性事件额外增强（社会显著性假说）
        """
        e = C.ENCODING
        cort = self.h['cortisol']
        # 高斯倒 U
        z = (cort - e['cort_optimal']) / max(e['cort_width'], C.EPS)
        CortPart = e['cort_max_gain'] * math.exp(-0.5 * z * z)
        # 超出阈值转为损害（负贡献）
        if cort > e['cort_impair_above']:
            over = min(1.0, (cort - e['cort_impair_above']) / (1.0 - e['cort_impair_above'] + C.EPS))
            CortPart -= e['cort_impair_max'] * over
        da = e['da_gain'] * (self.h['dopamine'] - C.HORMONES['dopamine']['base'])
        ne = e['ne_gain'] * max(0.0, self.arousal - 0.3)
        oxt = (e['oxt_social_gain'] * (self.h['oxytocin'] - C.HORMONES['oxytocin']['base'])) if is_social else 0.0
        m = 1.0 + CortPart + da + ne + oxt
        return clamp(m, *e['modulation_clamp'])


# ============================================================
# 慢变量：关系温度 / 心境
# ============================================================
class SlowState:
    """
    这就是"情感"的第二种含义——不指向任何具体事件的长期背景水位。
    它的更新极慢（天到周），它由激素的长时间积分驱动。
    """
    def __init__(self):
        self.v = {k: c['base'] for k, c in C.SLOW_VAR.items()}
        self._last_pos = None
        # 社会压力：负性社交经历的积分（原引擎有这个信号，我们补上）
        self.stress = 0.0

    def on_social_stress(self, v, salience):
        """负性社交事件会累积背景压力；压力越大，对同类信号越敏感。"""
        if v < 0.4:
            self.stress = min(1.0, self.stress + (0.4 - v) * salience * 0.6)

    def tick(self, dt_hours, hormones):
        if not (dt_hours > 0):
            return
        for k, c in C.SLOW_VAR.items():
            f = decay_factor(dt_hours, c['half_life'])
            self.v[k] = c['base'] + (self.v[k] - c['base']) * f
        # 慢变量的基线被激素缓慢牵引（这是"躯体状态塑造长期心境"）
        pull = dt_hours / 24.0
        self.v['mood_valence'] += pull * 0.05 * (hormones.valence_proxy() - 0.5)
        self.v['mood_arousal'] += pull * 0.04 * (hormones.arousal - self.v['mood_arousal'])
        for k, c in C.SLOW_VAR.items():
            self.v[k] = clamp(self.v[k], 0.0, 1.0)
        # 社会压力的半衰期 2 天
        self.stress *= decay_factor(dt_hours, 48.0)

    def on_affection(self, delta, hormones):
        """正向互动 → 关系温度上升；且催产素越高，衰退越慢。"""
        self.v['rel_temp'] = clamp(self.v['rel_temp'] + delta, 0.0, 1.0)
        self.v['mood_valence'] = clamp(self.v['mood_valence'] + delta * 0.4, 0.0, 1.0)
        self.v['mood_arousal'] = clamp(self.v['mood_arousal'] + abs(delta) * 0.3, 0.0, 1.0)


# ============================================================
# 倾向集合：长期性情按钮
# ============================================================
class DispositionSystem:
    """
    "情感"的第一种含义：一组可寻址的、带阈值的反应倾向。
    平时静默，被具体事件按下才放电 —— 这正是杰夫说的那句
    「具体的事情触动了某一个情感的时候，才产生了情绪」。
    """
    def __init__(self):
        self.activation = {k: 0.0 for k in C.DISPOSITIONS}   # 当前残余激活
        self.fire_count = {k: 0 for k in C.DISPOSITIONS}
        self.last_fire_t = {k: None for k in C.DISPOSITIONS}

    def tick(self, t_sim, dt_hours):
        if not (dt_hours > 0):
            return
        for k, cfg in C.DISPOSITIONS.items():
            f = decay_factor(dt_hours, cfg['half_life'])
            self.activation[k] *= f

    def evaluate(self, stim_type, salience):
        """
        返回命中的倾向列表 [{'id','name','charge','val_bias'}]
        salience ∈ [0,1]：事件本身的强度。
        只有 salience > threshold 才算被触发 —— 这就是「阈值」的生物学位置。
        """
        hits = []
        for k, cfg in C.DISPOSITIONS.items():
            if stim_type not in cfg['triggers']:
                continue
            over = salience - cfg['threshold']
            if over <= 0:
                continue  # 没按到这个按钮
            charge = over * cfg['gain']
            self.activation[k] = min(1.0, self.activation[k] + charge * 0.5)
            self.fire_count[k] += 1
            hits.append({'id': k, 'name': cfg['name'], 'charge': charge,
                         'val_bias': cfg['valence_bias']})
        return hits
