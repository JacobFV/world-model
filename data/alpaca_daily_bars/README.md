# alpaca_daily_bars

Alpaca adjusted daily bars since 2016.

**Source**: Alpaca Market Data v2 `GET /v2/stocks/bars` (timeframe=1Day, adjustment=all, feed=sip, 2016-01-01..2026-09-14) for 11,611 symbols: the original S&P 500/400 + 67 ETF list first, then every other non-test common/ordinary/ADR equity and ETF in the Nasdaq Trader directories of 2026-09-15 (warrants, rights, units, preferreds and notes excluded), equities before ETFs so a budget stop truncates only the ETF tail; 100 symbols per request with page_token pagination. 

**Credentials**: `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` (headers `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`; never recorded). **Licence**: Alpaca market data terms, personal/internal use; no redistribution or display.

**Evidence**: `close_price_total_return_adjusted` (USD/share, split and dividend adjusted; adjusted OHLC/VWAP/transactions attributes) and `volume_split_adjusted` (shares) on `ticker:US:<SYM>`, valid `[date, date+1)`. The universe is survivorship-biased (currently listed symbols only; delisted tickers are absent); adjustments reflect actions known at retrieval.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire alpaca_daily_bars --dry-run
wm acquire alpaca_daily_bars --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run alpaca_daily_bars
wm verify alpaca_daily_bars
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
