# Feature: 新闻资讯集成

## 背景

彭博终端的核心竞争力之一是**实时新闻资讯**，能够：
- 事件驱动交易：新闻 → 价格波动 → 交易机会
- 市场情绪分析：NLP 提取情绪指标
- 标的关联分析：新闻与具体标的关联

目前 TradeCat 缺少此功能，建议扩展。

## 目标

实现新闻聚合 + 情绪分析的 MVP，提升交易决策的信息维度。

---

## 推进方案

### 总体策略

**小步快跑，先跑通再迭代**

```
Week 1-2: Phase 1 新闻聚合 MVP（可演示）
Week 3:   Phase 2 标的关联（实用性）
Week 4:   Phase 3 情绪分析（增值功能）
```

---

### Phase 1: 新闻聚合 MVP（优先级最高）

**目标**：能采集、能存储、能展示

#### Step 1.1: 数据源调研与选择

| 数据源 | 推荐度 | 理由 |
|-------|--------|------|
| CryptoPanic API | ⭐⭐⭐ | 加密原生，免费额度够用 |
| 金十数据 RSS | ⭐⭐⭐ | 国内首选，速度快 |
| 华尔街见闻 RSS | ⭐⭐ | 美股覆盖好 |
| 财联社 RSS | ⭐⭐ | A股覆盖 |

**首批接入**：CryptoPanic（加密） + 金十数据（综合）

#### Step 1.2: 服务骨架搭建

```
services-preview/news-service/
├── src/
│   ├── __init__.py
│   ├── __main__.py           # 入口
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py           # 采集器基类
│   │   ├── cryptopanic.py    # CryptoPanic 采集
│   │   └── jin10.py          # 金十数据采集
│   ├── models.py             # 数据模型
│   ├── storage.py            # SQLite 存储
│   └── config.py             # 配置
├── scripts/
│   └── start.sh
├── Makefile
├── requirements.txt
└── pyproject.toml
```

#### Step 1.3: 数据模型

```python
# models.py
@dataclass
class NewsItem:
    id: str                    # 唯一ID (URL hash)
    title: str                 # 标题
    source: str                # 来源 (cryptopanic/jin10/...)
    url: str                   # 原文链接
    published_at: datetime     # 发布时间
    collected_at: datetime     # 采集时间
    content: str = ""          # 正文（可选）
    symbols: list[str] = []    # 关联标的（Phase 2）
    sentiment: str = ""        # 情绪（Phase 3）
    sentiment_score: float = 0.0
```

#### Step 1.4: SQLite Schema

```sql
CREATE TABLE news (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content TEXT,
    symbols TEXT,  -- JSON array
    sentiment TEXT,
    sentiment_score REAL
);

CREATE INDEX idx_news_published ON news(published_at DESC);
CREATE INDEX idx_news_source ON news(source);
```

#### Step 1.5: TUI 展示

在 TUI 新增页面（按键 `7` 新闻）：
- 新闻列表（时间倒序）
- 支持按来源筛选
- 显示标题 + 时间 + 来源

**交付物**：
- [ ] news-service 可采集 CryptoPanic + 金十
- [ ] 新闻存入 SQLite
- [ ] TUI 可查看新闻列表

---

### Phase 2: 标的关联

**目标**：新闻自动关联到相关标的

#### Step 2.1: 关键词映射表

```python
SYMBOL_KEYWORDS = {
    "BTCUSDT": ["比特币", "Bitcoin", "BTC", "btc"],
    "ETHUSDT": ["以太坊", "Ethereum", "ETH", "eth"],
    "NVDA": ["英伟达", "Nvidia", "NVDA"],
    # ...
}
```

#### Step 2.2: 标题匹配

```python
def extract_symbols(title: str, keywords: dict) -> list[str]:
    """从标题提取关联标的"""
    matched = []
    for symbol, words in keywords.items():
        if any(w.lower() in title.lower() for w in words):
            matched.append(symbol)
    return matched
```

#### Step 2.3: TUI 增强

- 新闻列表显示关联标的标签
- 点击新闻跳转到对应标详情

**交付物**：
- [ ] 新闻自动关联标的
- [ ] TUI 显示关联标签

---

### Phase 3: 情绪分析

**目标**：新闻情绪打分

#### Step 3.1: 情绪模型选择

| 方案 | 成本 | 准确度 | 速度 |
|-----|------|--------|------|
| 规则词库 | 免费 | 中等 | 快 |
| FinBERT | 免费 | 高 | 中 |
| GPT API | 付费 | 最高 | 慢 |

**推荐**：先用规则词库 MVP，后续升级 FinBERT

#### Step 3.2: 中文金融情绪词库

```python
POSITIVE = ["利好", "上涨", "突破", "增持", "看涨", "牛市"]
NEGATIVE = ["利空", "下跌", "暴跌", "减持", "看跌", "熊市"]
```

#### Step 3.3: 情绪聚合

- 单条新闻情绪 → 标的情绪 → 市场情绪
- 情绪时间序列（可用于回测）

**交付物**：
- [ ] 情绪打分功能
- [ ] 情绪指标展示

---

## 技术依赖

```txt
# requirements.txt
feedparser>=6.0.0      # RSS 解析
aiohttp>=3.8.0         # 异步 HTTP
jieba>=0.42.0          # 中文分词（Phase 2+）
transformers>=4.0.0    # FinBERT（Phase 3，可选）
```

---

## 风险与缓解

| 风险 | 缓解措施 |
|-----|---------|
| RSS 源不稳定 | 多源备份，采集失败告警 |
| 新闻量大存储膨胀 | 只保留 7 天，定时清理 |
| 情绪分析不准 | 先规则后模型，持续迭代 |

---

## 验收标准

| Phase | 验收标准 |
|-------|---------|
| Phase 1 | TUI 可查看最近 50 条新闻，刷新延迟 < 5s |
| Phase 2 | 标的关联准确率 > 80%（人工抽样 100 条） |
| Phase 3 | 情绪分析准确率 > 70%（人工抽样） |

---

## 下一步行动

1. [ ] 注册 CryptoPanic API Key
2. [ ] 搭建 news-service 骨架
3. [ ] 实现 CryptoPanic 采集器
4. [ ] 实现金十数据采集器
5. [ ] TUI 新增新闻页面

---

## 创建时间

2026-02-27
