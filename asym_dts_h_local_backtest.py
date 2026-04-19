"""
Route 2: 条件化 DTS 保护层
- WTS 参数校准完毕（W_FLAT=0.018, W_REV=0.012）
- DTS 只在"非 UP+UP 强多头周"生效，避免强势周被日线洗
- DOWN+DOWN 代码保留（结构对称），实际不可达
- 测试多组 DTS 参数 + 对比无 DTS 基线
"""
import pandas as pd
import numpy as np
from datetime import timedelta
from itertools import product

INDEX_CSV = '/home/node/a0/workspace/9f6b0b84-8364-43ba-9e79-f77b9e0902c7/workspace/uploads/000905_中证 500_日线数据.csv'
ETF_CSV = '/home/node/a0/workspace/9f6b0b84-8364-43ba-9e79-f77b9e0902c7/workspace/uploads/510500_中证_500ETF.csv'

idx_full = pd.read_csv(INDEX_CSV)
idx_full['date'] = pd.to_datetime(idx_full['date'])
idx_full = idx_full.sort_values('date').reset_index(drop=True)

etf = pd.read_csv(ETF_CSV)
etf = etf.rename(columns={'日期': 'date', '开盘': 'open', '收盘': 'close',
                          '最高': 'high', '最低': 'low'})
etf['date'] = pd.to_datetime(etf['date'])
etf = etf.sort_values('date').reset_index(drop=True)

START = pd.Timestamp('2023-04-17')
END = pd.Timestamp('2026-04-15')
WARMUP_START = START - timedelta(days=120)

idx = idx_full[(idx_full['date'] >= WARMUP_START) & (idx_full['date'] <= END)].reset_index(drop=True)
etf = etf[(etf['date'] >= START) & (etf['date'] <= END)].reset_index(drop=True)


def seven_rules(pc, cc, pa, ca, same, rev):
    if pc == 'FLAT' and cc == 'FLAT':
        return None
    if (pc in ['FLAT', 'UP'] and cc == 'UP') or (pc == 'UP' and cc == 'FLAT'):
        return 1
    if (pc in ['FLAT', 'DOWN'] and cc == 'DOWN') or (pc == 'DOWN' and cc == 'FLAT'):
        return 0
    d = abs(ca - pa)
    if pc == 'UP' and cc == 'UP':   return 1 if d >= same else None
    if pc == 'DOWN' and cc == 'DOWN': return -1 if d >= same else None
    if pc == 'UP' and cc == 'DOWN':  return -1 if d >= rev else None
    if pc == 'DOWN' and cc == 'UP':  return 1 if d >= rev else None
    return None


def amp(o, h, l):
    return (h - l) / o if o else 0


def classify(o, c, a, flat):
    if a < flat:
        return 'FLAT'
    return 'UP' if c >= o else 'DOWN'


def compute_wts_with_class(df, flat, same, rev):
    """
    返回：{week_num: (signal, prev_class, curr_class)}
    这样可以把周 K 分类传给 handle_data，用于判断是否启用 DTS 保护
    """
    df = df.copy()
    df['week'] = df['date'].dt.to_period('W-MON')
    df['week_num'] = df['date'].dt.strftime('%Y-%W')
    weekly = df.groupby('week').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'date': 'first', 'week_num': 'first'
    }).reset_index()
    week_counts = df.groupby('week').size()
    weekly['day_count'] = weekly['week'].map(week_counts)
    weekly = weekly[weekly['day_count'] >= 5].copy().reset_index(drop=True)
    if len(weekly) < 2:
        return {}
    signals = {}
    prev_sig = 1
    for i in range(1, len(weekly)):
        pr = weekly.iloc[i-1]
        cr = weekly.iloc[i]
        pa = amp(pr['open'], pr['high'], pr['low'])
        ca = amp(cr['open'], cr['high'], cr['low'])
        pc = classify(pr['open'], pr['close'], pa, flat)
        cc = classify(cr['open'], cr['close'], ca, flat)
        ns = seven_rules(pc, cc, pa, ca, same, rev)
        if ns is not None:
            prev_sig = ns
        signals[cr['week_num']] = (1 if prev_sig == 1 else 0, pc, cc)
    return signals


def compute_dts_daily(df, flat, same, rev):
    """返回 {date_ts: signal}"""
    df = df.sort_values('date').reset_index(drop=True)
    signals = {}
    prev_sig = 1
    for i in range(1, len(df)):
        pr = df.iloc[i-1]
        cr = df.iloc[i]
        pa = amp(pr['open'], pr['high'], pr['low'])
        ca = amp(cr['open'], cr['high'], cr['low'])
        pc = classify(pr['open'], pr['close'], pa, flat)
        cc = classify(cr['open'], cr['close'], ca, flat)
        ns = seven_rules(pc, cc, pa, ca, same, rev)
        if ns is not None:
            prev_sig = ns
        signals[cr['date']] = 1 if prev_sig == 1 else 0
    return signals


def backtest(W_FLAT, W_SAME, W_REV,
             D_FLAT, D_SAME, D_REV,
             use_dts=True,  # False = 纯 WTS 基线
             buy_fee=0.0003, sell_fee=0.0013, min_fee=5.0, capital=100000):
    trade_dates = idx[idx['date'] >= START]['date'].tolist()
    etf_map = dict(zip(etf['date'], etf['open']))
    idx_sorted = idx.sort_values('date').reset_index(drop=True)
    idx_date_list = idx_sorted['date'].tolist()

    # 预计算全量 DTS 信号（用"截至今天"的全量数据是允许的，因为我们只用"今天之前"）
    # 但为了严格无 look-ahead，我们在循环内递增算
    # 实际上 DTS 只依赖昨天和前天两根 K，可以预先算好全量
    dts_full = compute_dts_daily(idx_sorted, D_FLAT, D_SAME, D_REV)

    cash = capital
    shares = 0
    avg_cost = 0.0
    last_wts = 0
    wts_state = (1, 'FLAT', 'FLAT')  # (signal, prev_class, curr_class)
    wts_week_computed = None
    trades = []
    pending = None

    for today in trade_dates:
        # 执行昨日挂单
        if pending is not None and today in etf_map:
            p = etf_map[today]
            holding = shares > 0
            if pending == 1 and not holding:
                q = int(cash / p / (1 + buy_fee) / 100) * 100
                if q > 0:
                    cost = q * p
                    fee = max(cost * buy_fee, min_fee)
                    cash -= (cost + fee)
                    shares = q
                    avg_cost = p
                    trades.append((today, 'BUY', q, p, 0.0, fee))
            elif pending == 0 and holding:
                proceeds = shares * p
                fee = max(proceeds * sell_fee, min_fee)
                pnl = (p - avg_cost) * shares - fee
                cash += (proceeds - fee)
                trades.append((today, 'SELL', shares, p, pnl, fee))
                shares = 0
                avg_cost = 0.0
            pending = None

        current_week_num = today.strftime('%Y-%W')

        # WTS 重算（周一触发）
        if current_week_num != wts_week_computed:
            days_since_friday = (today.weekday() + 2) % 7
            if days_since_friday == 0:
                days_since_friday = 7
            last_friday = today - timedelta(days=days_since_friday)
            last_week_data = idx[idx['date'] <= last_friday].copy()

            if len(last_week_data) > 0:
                new_wts = compute_wts_with_class(last_week_data, W_FLAT, W_SAME, W_REV)
                if new_wts and current_week_num in new_wts:
                    wts_state = new_wts[current_week_num]
                elif new_wts:
                    wts_state = list(new_wts.values())[-1]
            wts_week_computed = current_week_num

        wts_signal, prev_class, curr_class = wts_state

        # DTS：用"上一个交易日"的 DTS（避免 look-ahead）
        # 找到今天在 idx_date_list 中的位置
        if today in dts_full:
            # 取 today 前一个交易日的 DTS
            try:
                today_idx = idx_date_list.index(today)
                if today_idx > 0:
                    prev_trading_day = idx_date_list[today_idx - 1]
                    dts_signal = dts_full.get(prev_trading_day, 1)
                else:
                    dts_signal = 1
            except ValueError:
                dts_signal = 1
        else:
            dts_signal = 1

        # === 状态机：条件化 DTS ===
        if wts_signal == 0:
            target = 0
        elif last_wts == 0 and wts_signal == 1:
            # 新多头周强制入场
            target = 1
        elif prev_class == 'UP' and curr_class == 'UP':
            # UP+UP 强多头保护：禁用 DTS
            target = 1
        elif prev_class == 'DOWN' and curr_class == 'DOWN':
            # 结构对称性保留（实际不可达，因为 WTS 已经 = 0）
            target = 0
        else:
            # 其他情况：允许 DTS 干预
            if use_dts:
                target = 1 if dts_signal == 1 else 0
            else:
                target = 1  # 纯 WTS 基线对比

        if today.weekday() == 4:
            last_wts = wts_signal

        holding = shares > 0
        if target == 1 and not holding:
            pending = 1
        elif target == 0 and holding:
            pending = 0
        else:
            pending = None

    final_date = trade_dates[-1]
    if shares > 0:
        fp = etf_map.get(final_date, avg_cost)
        equity = cash + shares * fp
    else:
        equity = cash
    return equity, trades


# ========== 测试 ==========
# 固定 WTS 校准参数
WF, WS, WR = 0.018, 0.009, 0.012

print("=" * 90)
print(f"WTS 参数固定：W_FLAT={WF}, W_SAME={WS}, W_REV={WR}")
print("=" * 90)

# 基线：Route 1 纯 WTS（不走 DTS 分支）
eq0, tr0 = backtest(WF, WS, WR, 0.005, 0.005, 0.005, use_dts=False)
ret0 = (eq0 - 100000) / 100000 * 100
print(f"\n基线 Route 1 纯 WTS:                          {ret0:>7.2f}%  ({len(tr0)} 笔)")

# Route 2：条件化 DTS
print("\n" + "=" * 90)
print("Route 2：条件化 DTS（UP+UP 保护，其他情景允许 DTS 出场）")
print("=" * 90)
print(f"{'D_FLAT':>8} {'D_SAME':>8} {'D_REV':>8}  {'收益率':>8}  {'笔数':>6}  {'vs 纯WTS':>10}")
print("-" * 90)

# DTS 参数网格
D_FLAT_grid = [0.003, 0.005, 0.007, 0.010]
D_SAME_grid = [0.003, 0.005, 0.008, 0.012, 0.020]
D_REV_grid  = [0.002, 0.003, 0.005, 0.008, 0.012]

results = []
for df_f, df_s, df_r in product(D_FLAT_grid, D_SAME_grid, D_REV_grid):
    eq, tr = backtest(WF, WS, WR, df_f, df_s, df_r, use_dts=True)
    ret = (eq - 100000) / 100000 * 100
    buys = sum(1 for t in tr if t[1] == 'BUY')
    sells = sum(1 for t in tr if t[1] == 'SELL')
    results.append({
        'D_FLAT': df_f, 'D_SAME': df_s, 'D_REV': df_r,
        'ret': ret, 'trades': buys + sells,
        'delta': ret - ret0
    })

df_res = pd.DataFrame(results).sort_values('ret', ascending=False).reset_index(drop=True)

# Top 15
print("\nTop 15 参数组合：")
for i, row in df_res.head(15).iterrows():
    print(f"{row['D_FLAT']:>8.4f} {row['D_SAME']:>8.4f} {row['D_REV']:>8.4f}  "
          f"{row['ret']:>7.2f}%  {int(row['trades']):>6}  {row['delta']:>+9.2f}pp")

# Bottom 5
print("\nBottom 5（最差）：")
for i, row in df_res.tail(5).iterrows():
    print(f"{row['D_FLAT']:>8.4f} {row['D_SAME']:>8.4f} {row['D_REV']:>8.4f}  "
          f"{row['ret']:>7.2f}%  {int(row['trades']):>6}  {row['delta']:>+9.2f}pp")

# 最优方案的交易明细
best = df_res.iloc[0]
print(f"\n{'='*90}")
print(f"最优 DTS 参数交易明细：D_FLAT={best['D_FLAT']}, D_SAME={best['D_SAME']}, D_REV={best['D_REV']}")
print(f"{'='*90}")
eq_b, tr_b = backtest(WF, WS, WR, best['D_FLAT'], best['D_SAME'], best['D_REV'], use_dts=True)
ret_b = (eq_b - 100000) / 100000 * 100
for t in tr_b:
    d, side, q, p, pnl, fee = t
    ds = pd.Timestamp(d).strftime('%Y-%m-%d')
    wd = pd.Timestamp(d).strftime('%a')
    print(f'  {ds} ({wd})  {side}  {q:>6d}@{p:.3f}  pnl={pnl:>10.2f}  fee={fee:.2f}')
print(f"\n合计：{len(tr_b)} 笔，收益 {ret_b:.2f}%")

# 保存
df_res.to_csv('/home/node/a0/workspace/9f6b0b84-8364-43ba-9e79-f77b9e0902c7/workspace/outputs/route2_dts_grid.csv', index=False)
print(f"\n完整 DTS 网格结果：outputs/route2_dts_grid.csv")
