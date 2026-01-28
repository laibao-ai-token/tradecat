# Adapter 处理说明

## 决策

- 为保持采集器可单文件运行，当前 impl 文件内嵌必要 adapter 逻辑  
- 所有配置读取统一经 `src/config.py` 的 `settings`  
- 不再跨文件引用旧 adapters 目录，避免路径耦合  

## 影响

- adapter 逻辑不丢失，但以“文件内实现”形式存在  
- 若后续需要共享抽取，可再集中迁移到 `src/adapters/`  

