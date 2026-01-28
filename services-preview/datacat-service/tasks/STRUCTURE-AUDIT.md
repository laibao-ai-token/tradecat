# 结构审计（新服务）

## 1. 层级定义（固定顺序）

```
source / market / scope / mode / direction / channel / type / impl.py
```

---

## 2. 允许枚举（硬性）

```
+-------------+------------------------------------------------------------+
| 层级        | 允许值                                                    |
|-------------+------------------------------------------------------------|
| source      | binance / third_party / internal                           |
| market      | spot / um_futures / cm_futures / options                   |
| scope       | all / symbol_group / symbol                                |
| mode        | realtime / backfill / sync                                 |
| direction   | push / pull / sync                                         |
| channel     | rest / ws / file / stream / kafka / grpc                   |
| type        | klines / trades / aggTrades / metrics / bookDepth /         |
|             | bookTicker / indexPriceKlines / markPriceKlines /           |
|             | premiumIndexKlines / alpha                                  |
| impl        | ccxt / cryptofeed / http / http_zip / raw_ws / official_sdk |
+-------------+------------------------------------------------------------+
```

---

## 3. 骨架一致性结论

- 已生成完整骨架与占位文件  
- 目录结构清单记录于：`tasks/src-structure.txt`  
- 粒度（interval/depth）全部下沉到实现文件内部  

