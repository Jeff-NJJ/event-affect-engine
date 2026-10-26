# -*- coding: utf-8 -*-
"""
数据化大脑 · 三层引擎
按杰夫的划分：
  第一层 Dispositions —— 可触发的长期倾向（一组带阈值的按钮）
  第二层 State        —— 全局慢变量（激素 + 心境 + 关系温度）
  第三层 Ledger       —— 不可压缩的历史认定（append-only）
"""

from .engine import DataBrain
from .state import HormonalSystem, SlowState, DispositionSystem
from .memory import Ledger, MemoryBucket, ReconsolidationEngine

__version__ = '0.1.0'
__all__ = ['DataBrain', 'HormonalSystem', 'SlowState', 'DispositionSystem',
           'Ledger', 'MemoryBucket', 'ReconsolidationEngine']
