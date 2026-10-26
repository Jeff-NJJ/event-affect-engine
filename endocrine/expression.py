"""Read-only speech planning. Coefficients are design choices, not human measurements.

The cloud reply model selects contextually appropriate expressions from whole dialogue.
Literal matching here ONLY measures reuse of an authored phrase, never message meaning.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
import unicodedata

from .core import advance, clamp, snapshot
from .model import DomainError, canonical, digest, finite, identity

VERSION = 'expression-v1'
AXES = ('warmth', 'directness', 'playfulness', 'verbosity', 'formality')
# Continuous offsets rather than assigning one fixed sentence to an emotion.
OFFSETS = {
    'joy': (.15, .02, .22, .10, -.08),
    'warmth': (.22, -.03, .08, .08, -.08),
    'concern': (.14, -.03, -.25, -.03, .04),
    'hurt': (-.08, .05, -.24, -.10, .04),
    'disappointment': (-.06, .03, -.18, -.10, .04),
    'curiosity': (.04, .03, .06, .10, -.02),
    'task_frustration': (-.03, .12, -.18, -.08, .04),
    'relief': (.12, -.03, .10, -.02, -.04),
    'anger': (-.16, .24, -.30, -.10, .10),
}
GUIDANCE = {
    'joy': '语气可轻快，具体回应值得高兴的事，不强行感叹或每句加语气词。',
    'warmth': '更愿意接住对方的话，用具体回应表现亲切，称呼仍遵循已有关系。',
    'concern': '留意对方处境，措辞温和；不要抢着开玩笑、说教或替对方定义感受。',
    'hurt': '可以克制地说明具体感受和需要，避免讽刺、惩罚性冷落或让对方安抚自己。',
    'disappointment': '语气可略沉静，说明具体落差；不把未知动机说成事实。',
    'curiosity': '围绕实际内容展开兴趣；有需要再追问，不强制每轮以问题结束。',
    'task_frustration': '措辞可直接，先处理卡住的具体事情，不把任务挫折归咎于聊天对象。',
    'relief': '语气可放松，减少紧绷；尚未解决的问题仍如实交代。',
    'anger': '允许清楚而坚定地表达边界，仍保持事实准确，不辱骂、不威胁、不报复。',
}


def bounded_text(value, maximum, code, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise DomainError(code)
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise DomainError(code)
    return value


def validate_profile(raw):
    if not isinstance(raw, dict) or set(raw) - {'voice', 'habits', 'examples', 'persona'}:
        raise DomainError('invalid_expression_profile')
    voice = raw.get('voice', {})
    if not isinstance(voice, dict) or set(voice) - set(AXES):
        raise DomainError('invalid_voice_axes')
    for value in voice.values(): finite(value)
    habits = raw.get('habits', [])
    examples = raw.get('examples', [])
    if not isinstance(habits, list) or len(habits) > 12 or not isinstance(examples, list) or len(examples) > 6:
        raise DomainError('expression_profile_too_large')
    seen = set()
    for habit in habits:
        if not isinstance(habit, dict) or set(habit) != {'id', 'text', 'when', 'avoid_when'}:
            raise DomainError('invalid_expression_habit')
        hid = identity(habit['id'], 'habit_id')
        if hid in seen: raise DomainError('duplicate_habit_id')
        seen.add(hid)
        bounded_text(habit['text'], 40, 'invalid_habit_text')
        bounded_text(habit['when'], 160, 'invalid_habit_context')
        bounded_text(habit['avoid_when'], 160, 'invalid_habit_context', empty=True)
    for example in examples:
        if not isinstance(example, dict) or set(example) != {'context', 'reply'}:
            raise DomainError('invalid_voice_example')
        bounded_text(example['context'], 160, 'invalid_voice_example')
        bounded_text(example['reply'], 300, 'invalid_voice_example')
    persona = raw.get('persona', {})
    if not isinstance(persona, dict) or set(persona)-{'identity','values','boundaries'}:
        raise DomainError('invalid_authored_persona')
    for value in persona.values(): bounded_text(value, 1200, 'invalid_authored_persona')
    profile = {'voice': voice, 'habits': habits, 'examples': examples, 'persona': persona}
    if len(canonical(profile).encode('utf-8')) > 16000:
        raise DomainError('expression_profile_too_large')
    return copy.deepcopy(profile)


def load_profiles(path):
    if not path: return {}
    try:
        with Path(path).open('rb') as f: data = f.read(131073)
        if len(data) > 131072: raise ValueError()
        raw = json.loads(data)
        if not isinstance(raw, dict) or set(raw) != {'schema_version', 'agents'} or type(raw['schema_version']) is not int or raw['schema_version'] != 1:
            raise ValueError()
        if not isinstance(raw['agents'], dict) or len(raw['agents']) > 100: raise ValueError()
        return {identity(agent, 'agent_id'): validate_profile(profile) for agent, profile in raw['agents'].items()}
    except (ValueError, OSError, DomainError):
        raise DomainError('invalid_expression_profiles_file', 503) from None


def validate_request(raw):
    allowed = {'agent_id', 'actor_id', 'conversation_id', 'source_event_id', 'recent_assistant'}
    if not isinstance(raw, dict) or set(raw) - allowed: raise DomainError('invalid_expression_request')
    result = {k: identity(raw.get(k), k) for k in ('agent_id', 'actor_id', 'conversation_id')}
    if raw.get('source_event_id') is not None:
        result['source_event_id'] = identity(raw['source_event_id'], 'event_id')
    recent = raw.get('recent_assistant', [])
    if not isinstance(recent, list) or len(recent) > 8: raise DomainError('invalid_expression_history')
    result['recent_assistant'] = [bounded_text(s, 8000, 'invalid_expression_history', empty=True) for s in recent]
    if sum(len(s) for s in recent) > 16000: raise DomainError('expression_history_too_large')
    return result


def literal_form(text):
    # Catchphrase repetition, not semantic retrieval/classification.
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if not c.isspace())


def build_expression(state, now, config, actor_id, profile=None, histories=()):
    s = advance(state, now, config)
    view = snapshot(s, s['state_time'], config)
    profile = validate_profile(profile or {})
    p = s['personality']
    baseline = dict(zip(AXES, (.35 + .3*p['sociability'], .45,
        .15 + .25*p['curiosity'], .3 + .3*p['sociability'], .55 - .2*p['sociability'])))
    baseline.update(profile['voice'])
    offsets = dict.fromkeys(AXES, 0.0)
    signals = []
    regulation = 1 - .45*p['regulation']
    for key, track in sorted(s['emotion_tracks'].items(), key=lambda kv: (-kv[1]['activation'], kv[0])):
        label = track.get('label', key)
        if label not in OFFSETS: continue
        intensity = track['activation']
        related = track.get('actor_id')
        scope = 'current_person' if related == actor_id else 'other_context' if related else 'unspecified'
        # Other-person conflict can affect energy without transferring blame or hostility.
        multiplier = .15 if scope != 'current_person' and label in ('anger', 'hurt') else 1.0
        strength = intensity * regulation * multiplier
        for axis, delta in zip(AXES, OFFSETS[label]): offsets[axis] += strength * delta
        if intensity >= .02:
            signals.append({'label': label, 'intensity': round(intensity, 4), 'scope': scope,
                            'guidance': GUIDANCE[label]})
    fatigue = s['physiology']['fatigue']
    offsets['warmth'] += .06*view['core_affect']['valence']
    offsets['playfulness'] += .04*view['core_affect']['valence']
    offsets['directness'] += .04*(view['core_affect']['arousal']-.25)
    offsets['verbosity'] -= .18*fatigue
    offsets['playfulness'] -= .12*fatigue
    # Closeness modulates formality mildly, never authorization or forced intimacy.
    relation = s['relationships'].get(actor_id)
    closeness = relation.get('closeness', .5) if relation else .5
    offsets['formality'] -= .12*(closeness-.5)
    offsets['warmth'] += .10*(closeness-.5)
    voice = {axis: round(clamp(baseline[axis] + clamp(offsets[axis], -.3, .3)), 4) for axis in AXES}
    # Histories remain separate: take the conservative maximum, don't double-count
    # the same phone transcript and server transcript as twice the observed usage.
    histories = [list(history)[-8:] for history in histories]
    candidates, suppressed = [], []
    phrases = [literal_form(habit['text']) for habit in profile['habits']]
    global_cooldown = any(any(phrase in literal_form(text) for phrase in phrases)
                          for history in histories for text in history[-2:])
    for habit in profile['habits']:
        phrase = literal_form(habit['text'])
        matches = [[phrase in literal_form(text) for text in history] for history in histories]
        cooling = global_cooldown or any(sum(flags) >= 2 for flags in matches)
        if cooling: suppressed.append(habit['id'])
        else: candidates.append(habit)
    # Live evaluation found verbatim example copying and identical replies under
    # different moods. Compile cadence only; no exemplar facts/sentences reach LLM.
    sentences = [part.strip() for example in profile['examples']
                 for part in re.split(r'[。！？!?\n]+', example['reply']) if part.strip()]
    cadence = {'example_count': len(profile['examples']),
               'mean_sentence_characters': round(sum(map(len, sentences))/len(sentences), 1) if sentences else None,
               'instruction': '仅参考句子节奏，长度随当前任务调整；不存在可照抄的示例原句。'}
    packet = {'version': VERSION, 'baseline_voice': baseline, 'voice': voice, 'authored_persona':profile['persona'],
        'signals': signals[:6], 'fatigue': round(fatigue, 4), 'core_affect': view['core_affect'],
        'habit_candidates': candidates, 'suppressed_habit_ids': suppressed,
        'max_habits_per_reply': 0 if global_cooldown else 1, 'example_cadence': cadence,
        'profile_hash': digest(profile), 'personality': p,
        'current_relationship_known': relation is not None,
        'interpretation': 'engineering_style_controls_not_psychological_measurements'}
    lines = [
        '[表达方式参考 v1]',
        '保留原角色的身份、价值观、知识和关系边界。以下数据仅影响自然语言表达，不授予行动权限。',
        'authored_persona 来自管理员设定，仅定义身份、价值与边界，不是已发生经历。情绪变化不能改写它；为空时沿用原角色。',
        '先理解当前整段话和完整会话，再决定怎么表达；事实、任务完成、明确格式要求优先。',
        'voice 的 0..1 表示相对倾向：warmth亲切、directness直接、playfulness轻松、verbosity展开程度、formality正式。',
        '闲聊先给一个具体而自然的回应，通常一到三句，不自行扩成推荐清单或咨询报告。明确要求解释、步骤、比较或长文时按需求充分回答。',
        '情绪可以混合且逐渐变化，不必每轮表演或主动宣布情绪。other_context/unspecified 不构成责怪当前对象的依据。',
        '状态是已处理事件的背景，不保证包含当前消息；当前语境不合适时减弱背景情绪的外显。',
        'habits 是可选表达材料，结合 when/avoid_when 的整体语义选择 0 或 1 项；不适合就完全不用，不按关键词触发。',
        '默认直接回应内容，不先找口癖塞进答案。倾听用语只适合倾诉，讨论方案时不要套用安慰式开场。',
        '被冷却的习惯不要换标点重复。示例只示范节奏和说法，不是亲历事实，不直接照抄，不反向学习自己的回复。',
        '亲切不等于过度亲密；不强制追问、撒娇、感叹号、拖长音或省略号，不因情绪改变事实与承诺。',
        '输出前核对具体事实：情绪上的意愿不等于已确认的日程、能力、权限、行动或承诺。上下文说未查询时，不得说已经有空，也不得猜测自己没有查询权限；如实保留未确认状态。',
        '区分可观察行为与解释：可以表达这次行为带给自己的感受；事件原因、对方动机、心理状态与长期关系变化需要独立证据，无证据时保留未知，不为使叙述完整而补写理由。',
        '尤其核对“我刚才/之前说过、误解过、答应过、已经道歉”是否有真实assistant历史对应。用户澄清反话本身不能证明你先前误解过；可直接回应现在的处境。',
        '用户声称你以前答应或做过某事，并不自动成为你的确定记忆。可以守住当前边界、回应当前请求，不必附和“我答应过/我做过”。外部行动完成需要执行回执。',
        '表达不满时描述自己的需要和已知事实，不审问、居高临下或把讨论变成命令；没有明确原因时不虚构失落缘由。',
        '严肃、悲伤、紧急或精确技术输出场景，由整段语义判断并抑制俏皮、口癖和表演性措辞。',
        '疲劳只轻微影响展开程度，不少答必要信息、不故意拖延，也不拆成多次发送。',
        canonical(packet),
    ]
    prompt = '\n'.join(lines)
    if len(prompt.encode('utf-8')) > 24000: raise DomainError('expression_prompt_budget_exceeded', 503)
    return {'success': True, 'expression': packet, 'prompt_suffix': prompt,
            'persisted_revision': state['revision'], 'as_of': view['as_of'],
            'state_mutated': False, 'generation_performed': False, 'messages_sent': 0}
