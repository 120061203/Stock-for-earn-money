"""依股價交易日精準對齊 FinMind 融資及法人資料，不補零、不往前挪日期。"""
import math
from api_errors import ApiLimitError

MARGIN = 'TaiwanStockMarginPurchaseShortSale'
INSTITUTION = 'TaiwanStockInstitutionalInvestorsBuySell'


def chip_values(margin, institution, today, previous, five_ago, symbol=None):
    errors = []
    def number(rows, day, field, name=None, nonnegative=False):
        matches = [row for row in rows if row.get('date') == day and (name is None or row.get('name') == name)
                   and (symbol is None or str(row.get('stock_id')) == symbol)]
        if len(matches) != 1:
            errors.append(f'{day} {name or "融資"}缺資料或重複')
            return None
        try:
            value = float(matches[0][field])
            if not math.isfinite(value) or (nonnegative and value < 0): raise ValueError()
            return value
        except (KeyError, TypeError, ValueError):
            errors.append(f'{day} {name or "融資"}數值無效')
            return None
    result = dict(margin_balance_today=number(margin,today,'MarginPurchaseTodayBalance',nonnegative=True),
                  margin_balance_5d_ago=number(margin,five_ago,'MarginPurchaseTodayBalance',nonnegative=True))
    for prefix, name in (('foreign','Foreign_Investor'),('trust','Investment_Trust')):
        for suffix, day in (('today',today),('yesterday',previous)):
            buy=number(institution,day,'buy',name,True)
            sell=number(institution,day,'sell',name,True)
            result[f'{prefix}_net_buy_{suffix}'] = None if buy is None or sell is None else buy-sell
    return result, list(dict.fromkeys(errors))


def fetch_chips(client, symbol, today, previous, five_ago, blocked):
    datasets, errors = {}, []
    for dataset in (MARGIN, INSTITUTION):
        if dataset in blocked:
            datasets[dataset] = []
            errors.append(blocked[dataset]); continue
        try:
            datasets[dataset] = client.fetch(dataset,symbol,five_ago,today)
        except ApiLimitError as exc:
            blocked[dataset] = str(exc)
            datasets[dataset] = []
            errors.append(str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            datasets[dataset] = []
            errors.append(str(exc))
    values, validation = chip_values(datasets[MARGIN],datasets[INSTITUTION],today,previous,five_ago,symbol)
    return values, list(dict.fromkeys(errors+validation))
