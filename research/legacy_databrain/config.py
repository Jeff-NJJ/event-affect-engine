# -*- coding: utf-8 -*-
"""
数据化大脑 · 集中配置

所有可调参数集中在此，便于做参数扫描与对照实验。
时间单位约定：
  - 模拟内部时间一律用 **小时**（float），从 t=0 开始
  - 涉及天数的参数在读取时换算
"""

# ============================================================
# 一、激素层（快变量）
# ============================================================
# half_life 单位=小时。参考真实动力学量级但不能照搬血浆半衰期：
#   皮质醇血浆半衰期 60-90min，但其**行为效应**（GR 介导的基因转录）持续数小时
#   所以我们用"效应半衰期"而非"清除半衰期"
HORMONES = {
    'oxytocin':   {'base': 0.40, 'half_life': 2.0,  'floor': 0.05, 'ceil': 1.0},
    'dopamine':   {'base': 0.30, 'half_life': 1.0,  'floor': 0.02, 'ceil': 1.0},
    'serotonin':  {'base': 0.50, 'half_life': 6.0,  'floor': 0.05, 'ceil': 1.0},
    'cortisol':   {'base': 0.20, 'half_life': 1.5,  'floor': 0.02, 'ceil': 1.0},
    'melatonin':  {'base': 0.10, 'half_life': 1.2,  'floor': 0.03, 'ceil': 1.0},
}

# 拮抗/协同矩阵：ANTAGONISM[源][目标] = 目标激素每小时获得的位移速率系数
ANTAGONISM = {
    'oxytocin':  {'cortisol': -0.10, 'melatonin': -0.02},
    'dopamine':  {'melatonin': -0.06, 'cortisol': -0.02},
    'serotonin': {'cortisol': -0.04, 'melatonin': -0.02},
    'cortisol':  {'oxytocin': -0.08, 'dopamine': -0.04, 'serotonin': -0.03},
    'melatonin': {'dopamine': -0.08, 'cortisol': -0.02, 'oxytocin': -0.01},
}

# 昼夜节律
CIRCADIAN = {
    # 皮质醇觉醒反应（CAR）：起床后 30-45min 达峰，约为全天谷值的 3-5 倍
    'cort_peak_hour': 7.5,      # 晨峰时刻
    'cort_amplitude': 0.22,     # 昼夜振幅（叠加在 base 上）
    'cort_trough_decay': 0.18,  # 从峰值下降的时间常数（小时）
    # 褪黑素：21-22点开始分泌，凌晨3点达峰，早晨被光抑制
    'mel_peak_hour': 3.0,
    'mel_amplitude': 0.35,
    'mel_floor_circ': 0.05,
    'mel_onset_hour': 21.0,     # 在此之前褪黑素节律为 0
    # 多巴胺昼夜基线
    'da_amplitude': 0.20,
    'da_peak_hour': 12.0,
}

# ============================================================
# 二、慢变量层（天-周尺度）
# ============================================================
SLOW_VAR = {
    # 关系温度 bonding：对一个客体的长期依恋强度
    'rel_temp':        {'base': 0.50, 'half_life': 240.0},   # 10 天半衰期
    # 心境效价 mood_valence：跨事件的、不指向具体对象的长期背景效价
    'mood_valence':    {'base': 0.50, 'half_life': 96.0},    # 4 天
    # 心境唤醒
    'mood_arousal':    {'base': 0.30, 'half_life': 36.0},    # 1.5 天
}
# （审查B6）OXY_REL_TEMP_PROTECT 已删除：引擎从未引用该常量，
# 留着只会让人误以为关系温度衰退受催产素保护。

# ============================================================
# 三、再巩固（经历驱动的旧判断更新）
# ============================================================
RECONSOLIDATION = {
    # --- 触发门控 ---
    'min_age_hours': 24.0,        # 记忆至少要有一天历史才配被重评（刚发生的不算"旧判断"）
    # 日常寒暄不算"经历"。只有够格的经历才有资格成为反证据 —— 这是杰夫定的门槛。
    'min_salience': 0.22,
    'subject_match_required': True,  # 必须是同一客体/主题的经历才算数
    'contrast_gate': 0.25,        # 新旧效价差要超过这个值才算"反证据"（预测误差门槛）

    # --- 阻抗：一条记忆有多难被改写 ---
    # impedance = age_factor + arousal_factor + repetition_factor
    'imp_age_scale': 150.0,       # 固化天数尺度
    'imp_age_max': 1.2,
    'imp_arousal_weight': 1.0,    # 原始唤醒越高越难改
    # 重复提取确实会加固记忆，但不能是主导项：否则长期陪伴本身就免疫了改写
    'imp_repeat_weight': 0.035,
    'imp_repeat_max': 0.60,

    # --- 反证据累积 ---
    # 不是一次就翻转，是累积到阈值才开窗
    'evidence_threshold_base': 0.62,
    'evidence_decay_half_life': 336.0,  # 反证据本身也会衰减；14天半衰期
    # 每次有效反证据的贡献 = salience * contrast * recency
    'evidence_gain': 1.0,

    # 阻抗对【阈值】的影响必须封顶且次线性。
    # 原先是 ×(1+imp) 的线性形式，而阻抗会随固化持续上涨 —— 那意味着
    # 记忆一旦超过某个深度就永远不可能再被更新，系统是死的。
    # 阻抗真正该作用的地方是【移动幅度】(见 impedance_damping)，
    # 而不是把门槛抬到永远够不着。
    'imp_threshold_slope': 0.60,
    'imp_threshold_cap': 2.50,

    # --- 窗口内的更新 ---
    'update_rate_base': 0.25,     # 基础学习率：一次重评最多移动 25% 的差距
    'max_shift_per_window': 0.30, # 单次窗口内的硬性移动上限（防翻转过猛）
    'window_hours': 24.0,         # 窗口持续时间
    'cooldown_hours': 72.0,       # 两次重评之间的冷却期
    # 深记忆需要更多次才动：实际移动量 = max_shift / (1 + impedance)
    'impedance_damping': True,

    # --- 保留性 ---
    'keep_original': True,        # 原始 appraise 永不删除（版本链）
    'max_appraisal_versions': 50,
}

# ============================================================
# 三之二、主题层（模式记忆）
# ============================================================
# 记忆的三个粒度：
#   客体级  P:汤姆          —— 我对这个人的整体认定
#   主题级  T:汤姆|敌意|工作 —— 我对"他在工作场合伤害我"这个模式的认定   ← 新增
#   情节级  E:某次事件      —— 具体发生了什么
#
# 主题层是"长期行为模式"真正落地的地方：
# 它不是任何一次事件，而是同类事件的累积形态。
THEME = {
    # 同一 (客体, 刺激类别, 场景) 出现几次才升格为"模式"
    'min_support': 3,
    # 模式一旦成形，就开始偏置后续同类事件的解读（确认偏误）
    # 这正是"命运"的机制：早期形成的模式会改变你之后看到的世界
    'confirmation_max': 0.12,
    'confirmation_ramp': 5.0,      # support 达到这个数量时拉力到最大
    # 模式比单次事件更顽固（但不能太高，否则模式永远锁死）
    'extra_impedance': 0.35,
    # 模式被反证后的改写更慢
    'update_rate_scale': 0.8,
    # 跨主题反证的权重：间接证据比直接证据弱，但必须够用。
    # 主题桶的证据供给天然比客体层少（只有同场景的不同类别事件），
    # 权重太低就永远攒不够。
    'cross_weight': 0.70,
}

# 刺激粗分类：把 20 种刺激归并成少数几类，主题才不会碎成沙
STIM_GROUP = {
    'praise': 'warm', 'affection': 'warm', 'gratitude': 'warm',
    'reconciliation': 'warm', 'shared_joy': 'warm', 'apology': 'warm',
    'playful_neg': 'warm',
    'curiosity': 'neutral', 'neutral': 'neutral', 'mundane': 'neutral',
    'provocation': 'hostile', 'criticism': 'hostile', 'conflict': 'hostile',
    'betrayal': 'hostile', 'rejection': 'hostile', 'humiliation': 'hostile',
    'neglect': 'neglect', 'loneliness': 'neglect',
    'loss': 'loss', 'crisis': 'loss',
}

# ============================================================
# 四、记忆衰减（对 Ombre-Brain 原公式的扩展）
# ============================================================
DECAY = {
    'lambda': 0.05,               # 原版：艾宾浩斯衰减率
    'threshold': 0.30,            # 原版：归档阈值
    'emotion_base': 1.0,          # 原版 base
    'arousal_boost': 0.80,        # 原版 arousal_boost

    # >>> 新增 1：效价偏离度 <<<
    # 原版只用 arousal，完全浪费了 valence。
    # 强度（无论正负）都该减慢衰减：|v-0.5|*2 ∈ [0,1]
    'valence_boost': 0.60,

    # >>> 新增 2：负性高唤醒的隧道效应 <<<
    # tunnel memory: 负性+高唤醒 → 中心细节被强化保存，外围细节快速丢失
    # 这里用 arousal 降低"细节保真度"，而不是提高得分
    'tunnel_arousal_gate': 0.65,
    'tunnel_valence_gate': 0.40,   # 低于此效价才算负性
    'tunnel_detail_loss': 0.35,    # 高负性高唤醒时，外围细节额外衰减比例

    'time_weight': {
        'd1': 1.0, 'd2': 0.9, 'floor': 0.3, 'k': 0.2197,
    },
    'resolved_factor': 0.05,       # 原版
    'urgency_boost': 1.5,          # 原版：高唤醒未解决桶优先浮现
    'urgency_arousal_gate': 0.70,  # 原版
}

# ============================================================
# 五、编码调制（把内分泌接进"写入"这一步）
# ============================================================
# 这是内分泌真正影响记忆的地方——**编码瞬间**的状态，而不是事后调整曲线
ENCODING = {
    # 皮质醇对巩固呈倒 U（文献核心结论）
    'cort_optimal': 0.45,          # GR 适度激活时巩固最强
    'cort_width': 0.28,            # 倒 U 宽度
    'cort_max_gain': 0.55,         # 峰值处的最大增益
    'cort_impair_above': 0.65,     # 超过此值转为损害（提取阶段的抑制更强）
    'cort_impair_max': 0.40,       # 最大损害幅度

    # 多巴胺（新颖性/显著性）→ VTA-海马 → 巩固增强
    'da_gain': 0.45,

    # 去甲肾上腺素代理 = max(arousal - 基线)：高唤醒 → BLA 调制增强
    'ne_gain': 0.40,

    # 催产素：社会性事件的编码增强（社会显著性假说）
    'oxt_social_gain': 0.35,

    # 总体调制上限，防止数值爆炸
    'modulation_clamp': (0.35, 2.2),
}

# ============================================================
# 六、刺激 → 情绪 映射（近似 Russell circumplex 坐标）
# ============================================================
# valence: 0=极负 1=极正 | arousal: 0=平静 1=激烈
STIMULUS_VA = {
    'praise':        (0.78, 0.45),
    'affection':     (0.88, 0.52),
    'gratitude':     (0.80, 0.38),
    'apology':       (0.62, 0.35),
    'reconciliation':(0.75, 0.55),
    'shared_joy':    (0.85, 0.68),
    'playful_neg':   (0.55, 0.48),
    'curiosity':     (0.58, 0.42),
    'neutral':       (0.50, 0.22),
    'mundane':       (0.48, 0.18),
    'provocation':   (0.35, 0.60),
    'criticism':     (0.28, 0.62),
    'conflict':      (0.18, 0.82),
    'betrayal':      (0.08, 0.88),
    'rejection':     (0.15, 0.78),
    'humiliation':   (0.10, 0.85),
    'neglect':       (0.32, 0.40),
    'loss':          (0.12, 0.72),
    'crisis':        (0.06, 0.92),
    'loneliness':    (0.28, 0.32),
}

# 每种刺激对激素的直接推动量
STIMULUS_HORMONE = {
    'praise':        {'dopamine': 0.12, 'serotonin': 0.08, 'oxytocin': 0.05},
    'affection':     {'oxytocin': 0.20, 'dopamine': 0.08, 'serotonin': 0.05},
    'reconciliation':{'oxytocin': 0.18, 'cortisol': -0.12, 'serotonin': 0.06},
    'shared_joy':    {'dopamine': 0.14, 'oxytocin': 0.10, 'serotonin': 0.05},
    'gratitude':     {'oxytocin': 0.08, 'serotonin': 0.10},
    'apology':       {'oxytocin': 0.10, 'cortisol': -0.08, 'serotonin': 0.05},
    'playful_neg':   {'oxytocin': 0.06, 'dopamine': 0.04, 'cortisol': 0.02},
    'curiosity':     {'dopamine': 0.06},
    'neutral':       {'dopamine': 0.02},
    'mundane':       {},
    'provocation':   {'cortisol': 0.10, 'dopamine': 0.04, 'oxytocin': 0.03},
    'criticism':     {'cortisol': 0.20, 'serotonin': -0.10, 'oxytocin': -0.05},
    'conflict':      {'cortisol': 0.28, 'oxytocin': -0.12, 'serotonin': -0.08},
    'betrayal':      {'cortisol': 0.35, 'oxytocin': -0.22, 'serotonin': -0.12, 'dopamine': -0.08},
    'humiliation':   {'cortisol': 0.32, 'oxytocin': -0.18, 'serotonin': -0.15},
    'rejection':     {'cortisol': 0.25, 'oxytocin': -0.15, 'dopamine': -0.10, 'serotonin': -0.08},
    'neglect':       {'oxytocin': -0.08, 'serotonin': -0.05, 'cortisol': 0.05},
    'loneliness':    {'oxytocin': -0.10, 'serotonin': -0.06, 'cortisol': 0.06},
    'loss':          {'cortisol': 0.30, 'oxytocin': -0.15, 'serotonin': -0.16, 'dopamine': -0.12},
    'crisis':        {'cortisol': 0.40, 'oxytocin': -0.20, 'serotonin': -0.18, 'dopamine': -0.10},
}

# ============================================================
# 七、倾向集合 Dispositions（长期性情按钮）
# ============================================================
# threshold: 触发门槛（越低越敏感）
# gain: 被触发后情绪响应的放大倍数
# valence_bias: 该倾向自身的情感色调（叠加到事件原始效价上）
DISPOSITIONS = {
    'abandonment':  {'name': '被抛弃恐惧', 'threshold': 0.30, 'gain': 1.6, 'valence_bias': -0.22,
                     'triggers': ['neglect', 'rejection', 'loneliness'], 'half_life': 12.0},
    'humiliation':  {'name': '羞耻敏感',   'threshold': 0.40, 'gain': 1.5, 'valence_bias': -0.20,
                     'triggers': ['criticism', 'humiliation', 'provocation'], 'half_life': 16.0},
    'injustice':    {'name': '不公义愤',   'threshold': 0.45, 'gain': 1.3, 'valence_bias': -0.10,
                     'triggers': ['betrayal', 'conflict'], 'half_life': 10.0},
    'approval':     {'name': '认可渴求',   'threshold': 0.25, 'gain': 1.2, 'valence_bias': 0.12,
                     'triggers': ['praise', 'gratitude'], 'half_life': 8.0},
    'intimacy':     {'name': '亲密需求',   'threshold': 0.28, 'gain': 1.2, 'valence_bias': 0.15,
                     'triggers': ['affection', 'shared_joy', 'reconciliation'], 'half_life': 10.0},
    'novelty':      {'name': '新奇探求',   'threshold': 0.35, 'gain': 1.1, 'valence_bias': 0.10,
                     'triggers': ['curiosity'], 'half_life': 4.0},
}

# ============================================================
# 七之二、人味层（让长期相处不显得奇怪）
# ============================================================
# 这一层的目标不是"更准"，而是"更像人"。
# 每一项的 enabled 都可以单独关掉，用来做消融对照。
HUMAN = {
    # 1. FAB：负性消退更快（Walker, Vogl & Thompson 1997）
    # 修复（审查 C4）：原实现把效价持续拉向 target=0.52，多年尺度 = 指数收敛，
    # 实测过半情节记忆被"自动美化"到 0.520 附近且无任何版本链痕迹——
    # 这违反 README 自己的宪法"情节层不改写，只衰减"。
    # FAB 的本义是**情感强度**（arousal）消退，不是效价改写。现在只衰减唤醒。
    'fab': {
        'enabled': True,
        'valence_drift': False,      # 旧版 True 的"玫瑰色回忆"开关，保留做消融
        'rate': 0.010,               # （消融用）每天往中性拉的比例
        'target': 0.52,
        'arousal_half_life_days': 30.0,
        'arousal_floor': 0.02,       # 修复：0.10 地板在多年尺度上把消退钳死，
                                     # 旧产物里过半情节钉在 0.100 → fab_decay=0.0 是钳制假象
    },
    # 2. 习惯化：重复的刺激越来越不管用
    'habituation': {
        'enabled': True,
        'scale': 12.0,              # 出现这么多次后衰减到 1/e
        'floor': 0.35,              # 再怎么重复也不会完全无感
        'decay_half_life': 12.0,    # 静默这么久习惯化减半（久别重逢会重新有感）
    },
    # 3. 情绪惯性与不应期
    'inertia': {
        'enabled': True,
        'k_base': 0.55,             # 单次事件能把当前情绪拉动多少
        'strong_a': 0.60,           # 唤醒超过此值算"强情绪"，触发不应期
        'refractory_hours': 6.0,    # 不应期时长
        'refractory_depth': 0.55,   # 不应期内响应被压低的最大幅度
        'rest_half_life': 3.0,      # 静默时情绪回落到静息位的半衰期（小时）
        # 静息水位本身的自然起伏 —— 没有它，平淡期会变成一条水平直线
        'rest_wobble': 0.080,
        'rest_wobble_days': 3.0,
    },
    # 4. 自发回忆
    'spontaneous': {
        'enabled': True,
        'base_rate_per_day': 1.2,   # 静默期每天自发想起某人的期望次数
        'mood_congruence': 0.60,    # 心境一致性提取的强度
        'congruence_width': 0.30,
        'emotion_pull': 0.12,       # 想起来的事对当下心情的扰动
    },
    # 5. 矛盾情感
    'ambivalence': {
        'enabled': True,
        'swing': 0.06,              # 态度摇摆幅度
        'period_days': 7.0,         # 摇摆周期
    },
    # 6. Zeigarnik：未完成的事记得更牢
    'zeigarnik': {
        'enabled': True,
        'boost': 1.35,
    },
}

# ============================================================
# 七·五、熟悉度：陪伴累积 与 疏远冷却
# ============================================================
# 为什么必须有这一层：
#   只用"再巩固"来改判断，等于说只有戏剧性事件才算数。可真实语料里
#   绝大多数互动是日常琐碎（salience 中位数在 0.1 以下），跑完多年尺度结果就是
#   几个熟人的认定全挤在千分之几的区间里 —— 分不出来，也不像人。
#   真人是靠几百次没什么内容的聊天慢慢变暖的（Zajonc 单纯曝光效应），
#   也是靠长期不联系慢慢变淡的。这两条都不需要"改写判断"那么大的动作，
#   它们是隐性的、缓慢的、会饱和的态度漂移 —— 所以不计入 revisions，
#   也不进版本链（没有哪个瞬间可以指认成"就是在这一刻改主意了"）。
FAMILIARITY = {
    # --- 陪伴累积 ---
    # 学习率用 Rescorla-Wagner 形式：越根深蒂固的看法，一次经历能推动的越少。
    # 注意不要用"接触次数"来衰减 —— 试过，876 次接触会把单次事件压到 10% 影响力，
    # 结果一次背叛只能让认定掉 0.004，等于没发生。"越熟越难改"该由阻抗来表达，
    # 而不是由次数表达（阻抗里已经有 age / arousal / repeat 三项）。
    'gain': 0.16,           # 基础学习率
    'salience_exp': 0.60,   # 显著性权重（次线性：越平淡的事影响越小，但不是零）
    'impedance_damp': 0.50, # 阻抗对学习率的抑制强度
    'max_drift': 0.26,      # 累积漂移的总幅度上限，防止跑飞
    # 单纯曝光偏置：见面本身就让人变暖，与这次聊的内容好坏无关
    'exposure_bias': 0.065,
    # 内容好坏与"变暖"的耦合程度（1.0=完全按内容，0=完全不看内容）
    'valence_coupling': 0.80,

    # --- 疏远冷却 ---
    'neglect_days': 21.0,   # 超过这么久没互动才开始变淡
    'cool_rate': 0.0022,    # 每天向中性回落的速率
    'cool_imp_relief': 0.35,  # 冷却能让阻抗松动到的上限（让人重新变得可改变）
    'cool_imp_rate': 0.010,   # 每天松动多少阻抗
    'neutral_point': 0.50,
}


# ============================================================
# 七·六、面向 agent 的输出层（自原内分泌引擎 v2.0 移植并多人化）
# ============================================================
# 这一段的机制来自杰夫最初给的「内分泌引擎 v2.0」，本来要接的是
# "一句话 → 激素 → 拼 prompt_suffix 给角色卡"。我们把它接进
# 数据化大脑时做了两处改动：
#   1. 原来只有一个全局 rel_temp（他的世界里只有一个人），
#      分离焦虑、熟悉度这些都改成**按客体**计算
#   2. 原来 Signed 是一次性施放，这里走 DelayedReleaser 排进时间轴，
#      于是"延迟反应"在多年尺度的模拟里也能正确发生
AGENT = {
    # --- 慢路径：拒绝/批评/忽视/挑衅之后，激素反应不是当场全部释放 ---
    'slow_path_delay': 0.70,      # 延迟部分的强度折扣
    'slow_path_hours': 2.0,       # 延迟多久释放（小时）

    # --- 对话节奏：短时间内连续互动 = 投入状态 ---
    'cadence_hours': 120 / 3600.0,   # 原版是 120 秒
    'cadence_oxy': 0.02,
    'cadence_da': 0.01,

    # --- 分离焦虑（Bowlby 依恋理论，阶梯式增强）---
    'separation_enabled': True,
    'separation_bond_base': 0.30,    # 关系越深，分离越痛的基准
    'separation_scale': 1.0,         # 总强度调节（0 关掉）

    # --- 行为冲动 ---
    'behavior_enabled': True,
    'stress_habit_cort': 0.55,       # 皮质醇超过此值，行为偏向习惯化
    'stress_habit_boost': 1.30,
    'extreme_cort': 0.90,            # 极端皮质醇：冷却减半

    # --- 语气惯性 ---
    'tone_inertia': 0.40,            # 不兼容的语气翻转会被拦住的概率权重

    # --- 对方状态识别后，对我们自身激素的轻微带动 ---
    # 原版只打标签不带动激素；这是接入后的扩展，
    # 因为"对方在生气"这件事本来就该让我们皮质醇升高
    'user_affect_enabled': True,
    'user_affect_scale': 1.0,
}


# ============================================================
# 八、数值安全
# ============================================================
EPS = 1e-9
