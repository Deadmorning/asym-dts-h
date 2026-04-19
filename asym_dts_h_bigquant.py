# BigQuant 策略：Asym-DTS H 版 中证 500 ETF 趋势跟踪
# ======================================================================
# 策略说明：
#   - 使用中证 500 指数 (000905.SH) 的周线/日线信号，交易中证 500 ETF (510500)
#   - H 版 = 对称 DTS（进出双向）+ UP+UP 周强持仓保护 + DOWN+DOWN 明示空仓
#   - 相对原 BigQuant v2 的关键修正：
#       1) D_SAME / D_REV 从 2.50 改为小数（真门槛），让 Rule 3/4 能触发
#       2) 前一个交易日按交易日索引取，而非 timedelta(days=1)，避免周一取到周日
#       3) WTS 聚合加 day_count >= 5 过滤，节假日短周不参与
#       4) WTS 只用"截至上周五"的数据，且周内缓存，避免任何前视与日内抖动
#       5) last_wts 只在周五更新，保证整周"新多头周入场"语义稳定
#       6) 加入 UP+UP 强持仓保护（主要 alpha 来源）和 DOWN+DOWN 明示空仓
# 回测区间：2023-04-17 ~ 2026-04-15
# 初始资金：100,000

from bigmodule import M, I


# ==================== 初始化函数 ====================
# @param(id="m5", name="initialize")
def m5_initialize_bigquant_run(context):
    from bigtrader.finance.commission import PerOrder

    # 手续费：买 0.03%，卖 0.13%，最低 5 元
    context.set_commission(PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5))

    # 标的
    context.index_instrument = '000905.SH'   # 中证 500 指数
    context.etf_instrument = '510500.SH'     # 中证 500 ETF

    # 周线参数（H 版校准）
    context.W_FLAT = 0.018   # 1.8%，周 K FLAT 门槛
    context.W_SAME = 0.009   # 0.90%，同向幅度差门槛（Rule 1/2）
    context.W_REV  = 0.012   # 1.20%，反向幅度差门槛（Rule 3/4）

    # 日线参数（H 版最优）
    context.D_FLAT = 0.007   # 0.7%，日 K FLAT 门槛
    context.D_SAME = 0.003   # 0.30%，日线同向差门槛
    context.D_REV  = 0.003   # 0.30%，日线反向差门槛（关键：原版是 2.50，永不触发）

    # 状态变量
    context.last_wts = 0                  # 上周五收盘时的 WTS（只在周五更新）
    context.wts_signal = 1                # 当前有效的 WTS 信号
    context.wts_prev_class = 'FLAT'       # 当前周的 prev_class
    context.wts_curr_class = 'FLAT'       # 当前周的 curr_class
    context.wts_week_computed = None      # 记录 WTS 本周是否已算过，避免重复

    # 排序数据
    context.data.sort_values('date', inplace=True)


# ==================== 盘前处理 ====================
# @param(id="m5", name="before_trading_start")
def m5_before_trading_start_bigquant_run(context, data):
    pass


# ==================== Tick 数据处理 ====================
# @param(id="m5", name="handle_tick")
def m5_handle_tick_bigquant_run(context, tick):
    pass


# ==================== 核心算法 ====================
def calculate_amplitude(open_price, high_price, low_price):
    """K 线振幅 = (high - low) / open"""
    if open_price == 0:
        return 0
    return (high_price - low_price) / open_price


def get_klass(row, amp_val, flat_thr):
    """K 线分类：UP / DOWN / FLAT"""
    if amp_val < flat_thr:
        return 'FLAT'
    return 'UP' if row['close'] >= row['open'] else 'DOWN'


def seven_rules(prev_class, curr_class, prev_amp, curr_amp, same_thr, rev_thr):
    """
    七规则趋势判定
    返回：+1(做多) / -1(做空) / 0(空仓) / None(不变更)
    """
    # Rule 7: FLAT + FLAT → 维持原仓
    if prev_class == 'FLAT' and curr_class == 'FLAT':
        return None

    # Rule 5: FLAT/UP + UP，或 UP + FLAT → 多
    if (prev_class in ['FLAT', 'UP'] and curr_class == 'UP') or \
       (prev_class == 'UP' and curr_class == 'FLAT'):
        return 1

    # Rule 6: FLAT/DOWN + DOWN，或 DOWN + FLAT → 空
    if (prev_class in ['FLAT', 'DOWN'] and curr_class == 'DOWN') or \
       (prev_class == 'DOWN' and curr_class == 'FLAT'):
        return 0

    amp_diff = abs(curr_amp - prev_amp)

    # Rule 1: UP + UP → 幅度差 >= SAME 才确认
    if prev_class == 'UP' and curr_class == 'UP':
        return 1 if amp_diff >= same_thr else None

    # Rule 2: DOWN + DOWN → 幅度差 >= SAME 才确认
    if prev_class == 'DOWN' and curr_class == 'DOWN':
        return -1 if amp_diff >= same_thr else None

    # Rule 3: UP → DOWN 反向
    if prev_class == 'UP' and curr_class == 'DOWN':
        return -1 if amp_diff >= rev_thr else None

    # Rule 4: DOWN → UP 反向
    if prev_class == 'DOWN' and curr_class == 'UP':
        return 1 if amp_diff >= rev_thr else None

    return None


def compute_dts_signal(df, flat_thr, same_thr, rev_thr):
    """
    计算日线 DTS 序列
    df: 含 date, open, high, low, close 的 DataFrame（按 date 升序）
    返回：{date_str: 0 or 1}
    """
    signals = {}
    prev_signal = 1
    for i in range(1, len(df)):
        pr = df.iloc[i - 1]
        cr = df.iloc[i]
        pa = calculate_amplitude(pr['open'], pr['high'], pr['low'])
        ca = calculate_amplitude(cr['open'], cr['high'], cr['low'])
        pc = get_klass(pr, pa, flat_thr)
        cc = get_klass(cr, ca, flat_thr)
        ns = seven_rules(pc, cc, pa, ca, same_thr, rev_thr)
        if ns is not None:
            prev_signal = ns
        signals[df.iloc[i]['date']] = 1 if prev_signal == 1 else 0
    return signals


def compute_wts_with_class(df, flat_thr, same_thr, rev_thr):
    """
    计算周线 WTS 序列，返回 {week_num_str: (signal, prev_class, curr_class)}

    H 版关键点：
      - df 应只包含 <= 上周五的数据（调用方保证），不使用当周任何数据
      - day_count >= 5 过滤节假日短周
      - 返回分类信息，供状态机做 UP+UP / DOWN+DOWN 判断
    """
    import pandas as pd

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df['week'] = df['date'].dt.to_period('W-MON')
    df['week_num'] = df['date'].dt.strftime('%Y-%W')

    weekly = df.groupby('week').agg({
        'open': 'first',
        'high': 'max',
        'low':  'min',
        'close': 'last',
        'date': 'first',
        'week_num': 'first'
    }).reset_index()

    week_counts = df.groupby('week').size()
    weekly['day_count'] = weekly['week'].map(week_counts)
    weekly = weekly[weekly['day_count'] >= 5].copy().reset_index(drop=True)

    if len(weekly) < 2:
        return {}

    signals = {}
    prev_signal = 1
    for i in range(1, len(weekly)):
        pr = weekly.iloc[i - 1]
        cr = weekly.iloc[i]
        pa = calculate_amplitude(pr['open'], pr['high'], pr['low'])
        ca = calculate_amplitude(cr['open'], cr['high'], cr['low'])
        pc = get_klass(pr, pa, flat_thr)
        cc = get_klass(cr, ca, flat_thr)
        ns = seven_rules(pc, cc, pa, ca, same_thr, rev_thr)
        if ns is not None:
            prev_signal = ns
        signals[cr['week_num']] = (1 if prev_signal == 1 else 0, pc, cc)
    return signals


# ==================== 每日交易逻辑 ====================
# @param(id="m5", name="handle_data")
def m5_handle_data_bigquant_run(context, data):
    import pandas as pd
    from datetime import timedelta

    current_dt = data.current_dt
    today_str = current_dt.strftime('%Y-%m-%d')
    current_week_num = current_dt.strftime('%Y-%W')

    # 指数数据
    index_data = context.data[context.data['instrument'] == context.index_instrument]
    if len(index_data) == 0:
        return

    index_sorted = index_data.sort_values('date').reset_index(drop=True)

    # 今天必须在数据里
    today_rows = index_sorted[index_sorted['date'] == today_str]
    if len(today_rows) == 0:
        return
    today_idx = int(today_rows.index[0])

    # ---------- DTS 信号（用上一个交易日的 DTS）----------
    if today_idx > 0:
        hist_data = index_sorted.iloc[:today_idx + 1]
        dts_signals = compute_dts_signal(
            hist_data,
            context.D_FLAT, context.D_SAME, context.D_REV
        )
        prev_trading_date = index_sorted.iloc[today_idx - 1]['date']
        dts_signal = dts_signals.get(prev_trading_date, 1)
    else:
        dts_signal = 1

    # ---------- WTS 信号（周内只算一次，使用截至上周五的数据）----------
    if current_week_num != context.wts_week_computed:
        # 推算"本周一往前推一天"那个周五
        weekday = current_dt.weekday()   # Mon=0 ... Sun=6
        days_since_friday = (weekday + 2) % 7
        if days_since_friday == 0:
            days_since_friday = 7
        last_friday = current_dt - timedelta(days=days_since_friday)
        last_friday_str = last_friday.strftime('%Y-%m-%d')

        last_week_data = index_sorted[index_sorted['date'] <= last_friday_str].copy()
        if len(last_week_data) > 0:
            new_wts = compute_wts_with_class(
                last_week_data,
                context.W_FLAT, context.W_SAME, context.W_REV
            )
            if new_wts:
                # 优先取 current_week 的条目（如果有），否则取最后一个
                if current_week_num in new_wts:
                    s, pc, cc = new_wts[current_week_num]
                else:
                    s, pc, cc = list(new_wts.values())[-1]
                context.wts_signal = s
                context.wts_prev_class = pc
                context.wts_curr_class = cc
        context.wts_week_computed = current_week_num

    wts_signal = context.wts_signal
    prev_class = context.wts_prev_class
    curr_class = context.wts_curr_class

    # ---------- H 版状态机 ----------
    # 1) WTS=0 → 强制空仓
    # 2) 新多头周（last_wts=0 → wts=1）→ 强制入场（不看 DTS）
    # 3) UP+UP 强多头周 → 强制持仓保护（不看 DTS）
    # 4) DOWN+DOWN 周 → 明示空仓（实际已被 1) 覆盖，保留结构对称）
    # 5) 其他情景（FLAT±UP 弱多头、UP+DOWN 未触发反转、FLAT+FLAT 等）→ DTS 双向

    if wts_signal == 0:
        target_position = 0
    elif context.last_wts == 0 and wts_signal == 1:
        target_position = 1
    elif prev_class == 'UP' and curr_class == 'UP':
        target_position = 1
    elif prev_class == 'DOWN' and curr_class == 'DOWN':
        target_position = 0
    else:
        target_position = 1 if dts_signal == 1 else 0

    # last_wts 只在周五更新（H 版关键修正）
    if current_dt.weekday() == 4:   # Friday
        context.last_wts = wts_signal

    # ---------- 执行交易 ----------
    holding = list(context.get_account_positions().keys())

    if target_position == 1 and context.etf_instrument not in holding:
        context.order_target_percent(context.etf_instrument, 1.0)
    elif target_position == 0 and context.etf_instrument in holding:
        context.order_target_percent(context.etf_instrument, 0)


# ==================== 成交回报处理 ====================
# @param(id="m5", name="handle_trade")
def m5_handle_trade_bigquant_run(context, trade):
    pass


# ==================== 委托回报处理 ====================
# @param(id="m5", name="handle_order")
def m5_handle_order_bigquant_run(context, order):
    pass


# ==================== 盘后处理 ====================
# @param(id="m5", name="after_trading")
def m5_after_trading_bigquant_run(context, data):
    pass


# ==================== 模块定义 ====================
# @module(position="-555,-665", comment="""中证 500 指数日线数据""")
m1 = M.input_features_dai.v30(
    input_1=None,
    mode="""SQL""",
    sql="""
SELECT
    date,
    instrument,
    open,
    high,
    low,
    close,
    volume
FROM cn_stock_index_bar1d
WHERE instrument = '000905.SH'
ORDER BY date, instrument
    """,
    expr="""""",
    expr_filters="""""",
    expr_tables="""cn_stock_index_bar1d""",
    extra_fields="""date, instrument""",
    order_by="""date, instrument""",
    expr_drop_na=True,
    extract_data=False,
    m_name="""m1"""
)

# @module(position="-240,-465", comment="""抽取预测数据""")
m2 = M.extract_data_dai.v20(
    sql=m1.data,
    start_date="""2023-04-17""",
    start_date_bound_to_trading_date=True,
    end_date="""2026-04-15""",
    end_date_bound_to_trading_date=True,
    before_start_days=90,
    keep_before=False,
    debug=False,
    m_name="""m2"""
)

# @module(position="-295,-365", comment="""交易，日线，Asym-DTS H 版""")
m5 = M.bigtrader.v53(
    data=m2.data,
    start_date="""2023-04-17""",
    end_date="""2026-04-15""",
    initialize=m5_initialize_bigquant_run,
    before_trading_start=m5_before_trading_start_bigquant_run,
    handle_tick=m5_handle_tick_bigquant_run,
    handle_data=m5_handle_data_bigquant_run,
    handle_trade=m5_handle_trade_bigquant_run,
    handle_order=m5_handle_order_bigquant_run,
    after_trading=m5_after_trading_bigquant_run,
    capital_base=100000,
    frequency="""daily""",
    product_type="""股票""",
    rebalance_period_type="""交易日""",
    rebalance_period_days="""1""",
    rebalance_period_roll_forward=True,
    backtest_engine_mode="""标准模式""",
    before_start_days=0,
    volume_limit=1,
    order_price_field_buy="""open""",
    order_price_field_sell="""open""",
    benchmark="""000905.SH""",
    plot_charts="""全部显示""",
    debug=False,
    backtest_only=False,
    m_name="""m5"""
)
