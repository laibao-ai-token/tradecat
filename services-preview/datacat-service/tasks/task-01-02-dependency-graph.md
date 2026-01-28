# 任务 01-02：依赖关系图（模块级）

## 目标

- 标注每个模块的内部/外部依赖。

## 输出

- 依赖图（文本）

## 依赖图（模块级）

- collectors/ws.py
  - 内部：adapters.ccxt, adapters.cryptofeed, adapters.metrics, adapters.timescale, config
  - 交叉：collectors.backfill（GapScanner/RestBackfiller/ZipBackfiller）
  - 外部：cryptofeed

- collectors/metrics.py
  - 内部：adapters.ccxt, adapters.metrics, adapters.rate_limiter, adapters.timescale, config
  - 外部：requests

- collectors/backfill.py
  - 内部：adapters.ccxt, adapters.metrics, adapters.rate_limiter, adapters.timescale, config
  - 外部：requests, csv, zipfile

- collectors/alpha.py
  - 内部：adapters.rate_limiter, config
  - 外部：aiohttp, json

- collectors/downloader.py
  - 外部：requests

- adapters/ccxt.py
  - 内部：adapters.rate_limiter, libs/common/symbols
  - 外部：ccxt

- adapters/cryptofeed.py
  - 内部：config
  - 外部：cryptofeed

- adapters/timescale.py
  - 内部：config
  - 外部：psycopg, psycopg_pool

- adapters/rate_limiter.py
  - 外部：fcntl, threading, os

- adapters/metrics.py
  - 外部：threading, time

- config.py
  - 外部：os, pathlib

- __main__.py
  - 内部：config
  - 外部：subprocess, signal

## 验收

- 依赖关系可追溯。
