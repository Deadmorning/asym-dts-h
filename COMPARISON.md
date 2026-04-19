# 四方策略结构对比：BigQuant 原版 / BigQuant fixed / GitHub asym_v2 / H 版

本文详列四个实现在结构层面的差异，**不只是前视偏差**。

## 一、结构对照表

| 维度 | BigQuant 原版 | BigQuant fixed-4 | GitHub asym_v2 | **H 版（本仓库）** |
|---|---|---|---|---|
| WTS 重算频率 | 每日 | 每日 | 周一一次 | 周一一次 |
| WTS 聚合数据边界 | 截止当日全量（含本周部分 K） | 同左 | `≤ 上周五` | `≤ 上周五` |
| WTS 完整周过滤 | 无 | 无 | `day_count≥5` | `day_count≥5` |
| DTS 计算方式 | 每日全量重算 O(N²) | 每日全量重算 O(N²) | 回测开始一次性 O(N) | 每日滚动 O(N) 或一次性 |
| `last_wts` 更新时机 | **每日更新** | **每日更新** | 周五更新 | 周五更新 |
| D_SAME / D_REV | 2.50 / 2.50（bug） | 0.025 / 0.025 | 0.005 / 0.004 | 0.003 / 0.003 |
| W_FLAT / W_REV | 0.015 / 0.0045 | 0.015 / 0.0045 | 0.018 / 0.012 | 0.018 / 0.012 |
| 前一日取法 | `timedelta(days=1)`（bug） | 按交易日索引 | 按交易日索引 | 按交易日索引 |
| **UP+UP 保护** | 无 | 无 | 无 | **有** |
| **DOWN+DOWN 明示空仓** | 无 | 无 | 无 | **有** |
| 状态机本质 | 新多头强入 + DTS 双向（被 bug 破坏） | 新多头强入 + DTS 双向 | 新多头强入 + DTS 双向 | **+ UP+UP / DOWN+DOWN 分支** |

## 二、除"前视偏差"外的 5 个核心结构问题

### 1. `last_wts` 每日刷新 —— V 底捕获率暴跌

**原 BigQuant**：
```python
context.last_wts = wts_signal  # 每日末尾
```
因为 WTS 每日也在重算，周一当周数据还不完整时，WTS 可能为 1，`last_wts` 立即跟着变成 1。下一日再看就是 `last_wts=1, wts=1` → "新多头周强入"分支永远不会触发。

**H 版**：
```python
if current_dt.weekday() == 4:   # Friday only
    context.last_wts = wts_signal
```
保证整周 `last_wts=0` 未变更，V 底整周都有强制入场权。

**实证**：2024-02-06 V 底这一笔净赚 +14,621；L 版（DTS 确认入场）错过这笔后净赚 -2,048，单笔差 -16,599。

---

### 2. WTS 聚合不过滤短周 —— 节假日信号污染

**原 BigQuant**：
```python
weekly = df.groupby('week').agg(...).reset_index()   # 不过滤
```
节假日 2-4 天的短周（春节、国庆、五一）也算周 K，振幅/方向异常会误导 WTS。

**H 版**：
```python
weekly['day_count'] = weekly['week'].map(week_counts)
weekly = weekly[weekly['day_count'] >= 5]
```
只保留完整 5 日周，A 股每年约 5-6 次节假日这才稳定。

---

### 3. 日内状态机抖动

原 BigQuant 每天都在重算 WTS + 每天都更新 `last_wts` → 周内状态机反复翻转，违反"周线是低频信号"的设计原则。

H 版通过"周一算一次并缓存 + 周五更新 last_wts"彻底堵住这个口。

---

### 4. WTS 字典键是"那周第一天"，查找逻辑隐含偏移

**原 BigQuant**：
```python
week_start = weekly.iloc[i]['date'].strftime('%Y-%m-%d')
signals[week_start] = ...
# 查找：for date_str, signal in sorted(wts_signals.items(), reverse=True):
#           if date_str <= today_str: ...
```
只要今天 ≥ 本周一，就会取到本周的 WTS。但"本周 WTS"理论上要等本周收盘才完整。**即使没有前视偏差，语义也是"用本周部分 K 算出的 WTS"，而 H/GitHub 用的是"上周完整周 K 的 WTS"**。

---

### 5. D_REV 参数量级错误

**原 BigQuant**：`D_SAME = D_REV = 2.50` → `amp_diff >= 2.50` 永远不成立 → Rule 3/4（UP↔DOWN 反转）**从来不触发** → DTS 实际退化为只有 Rule 5/6 的"跟随"信号，无法逆向翻转。

**BigQuant fixed**：`0.025` → 还是比 H 的 `0.003` 高一个数量级，大部分反转 K 依然被吞。

**H 版**：`0.003` → DTS 反转真正生效，震荡市套利才能发生。

---

## 三、四方状态机简写对比

```python
# ────── BigQuant 原版（含 2 bug） ──────
if wts == 0:                    target = 0
elif last_wts == 0 and wts == 1: target = 1     # 但 last_wts 日更常被吞
else:                            target = DTS   # 但 D_REV=2.50 → DTS 残废

# ────── BigQuant fixed-4 ──────
if wts == 0:                    target = 0
elif last_wts == 0 and wts == 1: target = 1     # 依旧 last_wts 日更问题
else:                            target = DTS

# ────── GitHub asym_v2 ──────
if wts == 0:                    target = 0
elif last_wts == 0 and wts == 1: target = 1     # last_wts 周更，稳
else:                            target = DTS

# ────── H 版 ──────
if wts == 0:                    target = 0
elif last_wts == 0 and wts == 1: target = 1     # 捕 V 底
elif UP+UP:                     target = 1     # ★ 强多头保护（最大 alpha）
elif DOWN+DOWN:                 target = 0     # 结构对称保留
else:                            target = DTS   # 震荡市套利
```

## 四、实盘化后 BigQuant 与 H 版的收益差距来源

即使把 BigQuant 原版的两个 bug 全部修好（fixed-4 已经做到），它相对 H 版还差：

| 差距 | 量化影响 | 机制 |
|---|---|---|
| `last_wts` 日更 → 周更 | +5 ~ +10pp | V 底捕获率从 50% 升到 ~100% |
| WTS 短周过滤 | +1 ~ +3pp | 减少节假日后误信号 |
| UP+UP 强持仓保护 | +20 ~ +25pp | 防止强多头周被日内洗仓 |
| D_REV 从 0.025 降到 0.003 | +3 ~ +5pp | DTS 反转真正生效 |
| **合计相对差距** | **~29pp** | GitHub 40.39% → H 64.80-69.78% |

## 五、核心结论

1. **前视偏差只决定"回测数字能不能信"，不决定"策略好不好"**。BigQuant 版回测数字虚高，但即使在无前视偏差的条件下它本身的设计也远差于 H 版。

2. **BigQuant fixed-4 的状态机本质 = GitHub asym_v2**，收益上限约 40%。

3. **H 版相对 GitHub/BigQuant 的 +20~+25pp alpha 主要来自 UP+UP 强持仓保护**，这是 H 版的独家设计。

4. **`last_wts` 周更 是 V 底捕获的前提**。任何周线状态更新不是"周粒度"的设计（BigQuant 日更 last_wts），都会严重削弱新多头周强入场分支。

5. **UP+UP 保护不能退化为"持仓时 DTS 可以出场"**。我们实验证明放开 DTS 出场权反而把收益从 64% 干到 34%，因为 T+1 机制下暴涨后暴跌救不回来，反而多了震荡市磨损。
