# Datacat Service 运行基线报告

- 时间：2026-01-29T11:43:56Z
- 系统：Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39
- CPU：x86_64
- 负载：(1.1943359375, 1.576171875, 1.01611328125)

## 进程快照

```
 662846       05:59  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && nohup timeout 24h python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid && nohup timeout 24h python3 src/collectors/binance/um_futures/all/realtime/pull/rest/metrics/http.py >> logs/prod-metrics-24h.log 2>&1 & echo $! > pids/prod-metrics-24h.pid
 662850       05:59  0.0  0.0 timeout 24h python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 662854       05:59  0.3  0.2 python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 743712       05:05  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && nohup timeout 24h python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid
 743716       05:05  0.0  0.0 timeout 24h python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 743718       05:04  0.3  0.2 python3 src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 772939       04:43  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && nohup timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid
 772943       04:43  0.0  0.0 timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 772945       04:43  0.3  0.3 ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 843472       03:44  0.0  0.0 bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && nohup timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 & echo PID=$!; sleep 1; ps -p $! -o pid,cmd
 843475       03:44  0.0  0.0 timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 843477       03:44  0.3  0.3 ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 887432       03:12  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && nohup timeout 24h sleep 300 >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid
 964845       02:22  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && nohup timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid; ls -la logs/prod-ws-24h.log
 964848       02:22  0.0  0.0 timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 964850       02:22  0.4  0.3 ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 995357       02:02  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && export PYTHONPATH="/home/lenovo/.projects/tradecat/libs:/home/lenovo/.projects/tradecat/services-preview/datacat-service/src" && export DATACAT_OUTPUT_MODE=db && setsid timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py >> logs/prod-ws-24h.log 2>&1 < /dev/null & echo $! > pids/prod-ws-24h.pid && ls -la logs/prod-ws-24h.log
 995361       02:02  0.0  0.0 timeout 24h ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
 995363       02:02  0.5  0.3 ./.venv/bin/python src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
1011863       01:49  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && timeout 24h sleep 300 >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid && ls -la logs/prod-ws-24h.log
1032645       01:36  0.0  0.0 /bin/bash -lc cd /home/lenovo/.projects/tradecat/services-preview/datacat-service && mkdir -p logs pids && sleep 300 >> logs/prod-ws-24h.log 2>&1 & echo $! > pids/prod-ws-24h.pid && ls -la logs/prod-ws-24h.log
1047338       01:24  0.0  0.0 timeout 86400 python3 /home/lenovo/.projects/tradecat/services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
1047341       01:24  0.6  0.2 python3 /home/lenovo/.projects/tradecat/services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
1052822       01:20  0.0  0.0 timeout 86400 bash -lc    end=$((SECONDS+86400))   while [ $SECONDS -lt $end ]; do     python3 "/home/lenovo/.projects/tradecat/services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/pull/rest/metrics/http.py" >> "/home/lenovo/.projects/tradecat/services-preview/datacat-service/logs/metrics-24h.log" 2>&1     sleep 300   done 
1052826       01:20  0.0  0.0 bash -lc    end=$((SECONDS+86400))   while [ $SECONDS -lt $end ]; do     python3 "/home/lenovo/.projects/tradecat/services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/pull/rest/metrics/http.py" >> "/home/lenovo/.projects/tradecat/services-preview/datacat-service/logs/metrics-24h.log" 2>&1     sleep 300   done 
```
