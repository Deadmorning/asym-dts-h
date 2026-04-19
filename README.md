# Asym-DTS H 版策略

中证 500 ETF (510500) 基于 000905.SH 指数的双层趋势跟踪策略。

**回测表现**：2023-04-17 ~ 2026-04-15，10 万初始资金 → **+64.80%（97 笔交易）**，相对纯 WTS 基线 +13.33pp。

---

## 核心设计

两层信号的分工：

- **周线 WTS（战略层）**：决定是否允许做多
- **日线 DTS（战术层）**：在多头周内做短线进出决策

H 版状态机：

```
if wts == 0:                      空仓
elif 新多头周 (last_wts=0→1):      强制入场   ← V 底捕获
elif UP+UP 强多头周:               强制持仓   ← 主要 alpha 来源
elif DOWN+DOWN:                   空仓      ← 结构对称
else:                              DTS 双向   ← 震荡市套利
```

相对原 BigQuant v2 修复了 6 处结构问题（D_REV 量级、前一日取法、WTS 短周过滤、WTS 缓存、last_wts 周更、UP+UP 保护）。

详见 [REPORT.md](REPORT.md) 和 [COMPARISON.md](COMPARISON.md)。

---

## 文件结构

```
asym-dts-h/
├── asym_dts_h_bigquant.py          # BigQuant 平台主文件（模块化，可直接上线回测）
├── asym_dts_h_local_backtest.py    # 本地 pandas 向量化回测（用于参数校准）
├── REPORT.md                       # 完整策略报告（设计 + 回测 + 风险）
├── COMPARISON.md                   # 四方策略结构对比
└── README.md                       # 本文件
```

---

## 快速上手

### BigQuant 平台
1. 登录 https://bigquant.com
2. 新建一个 AIStudio 项目，复制 `asym_dts_h_bigquant.py` 全部内容
3. 点击回测，区间 `2023-04-17` ~ `2026-04-15`
4. 初始资金 100,000，手续费按 PerOrder(买 0.03%, 卖 0.13%, 最低 5 元)

BigQuant 平台文档：https://bigquant.com/wiki/collection/SkPzwuq9oo

### 本地回测
```bash
# 需要 pandas, numpy
python asym_dts_h_local_backtest.py
```

数据需要放在以下路径（或修改脚本开头的 `INDEX_CSV` / `ETF_CSV`）：
- `uploads/000905_中证 500_日线数据.csv`：字段 `date, open, high, low, close`
- `uploads/510500_中证_500ETF.csv`：字段 `日期, 开盘, 收盘, 最高, 最低`

---

## 参数

当前校准值（2023-04 ~ 2026-04 三年期最优）：

| 参数 | 值 | 说明 |
|---|---|---|
| `W_FLAT` | 0.018 | 周 K FLAT 门槛（1.8%） |
| `W_SAME` | 0.009 | 周 K 同向差门槛 |
| `W_REV`  | 0.012 | 周 K 反向差门槛 |
| `D_FLAT` | 0.007 | 日 K FLAT 门槛（0.7%） |
| `D_SAME` | 0.003 | 日 K 同向差门槛 |
| `D_REV`  | 0.003 | **日 K 反向差门槛（DTS 灵敏度关键）** |

建议实盘每 6-12 个月用滚动窗口重新校准 `D_REV`，其他参数在合理区间内相对稳定。

---

## 为什么 H 版每个分支都必要

消融实验（见 COMPARISON.md 第四节）：

| 变体 | 收益 | 结论 |
|---|---|---|
| H 版（完整） | **64.80%** | baseline |
| 去掉 UP+UP 保护（= GitHub asym_v2） | 40.39% | 主要 alpha 来源 |
| 新多头周 DTS 确认入场（L 版） | 30.25% | 漏掉 V 底 |
| DTS 只出不进（非对称） | 43.35% | 震荡市套利被阉割 |
| UP+UP 放开 DTS 出场 | 34.38% | T+1 下反而磨损加剧 |
| 部分非对称 | 48.03% | 多输入少输出 |

---

## 风险提示

- 策略在 A 股特殊机制（T+1、涨跌停、节假日）下设计，不直接迁移到美股/加密
- 不能躲"单日情绪极端反转"黑天鹅（典型例：2024-10-09 开盘进场 → 后续暴跌 -7,749）
- 回测数据含 2026-04-19 之后部分区间，参数校准不要使用未来数据

---

## License

MIT
