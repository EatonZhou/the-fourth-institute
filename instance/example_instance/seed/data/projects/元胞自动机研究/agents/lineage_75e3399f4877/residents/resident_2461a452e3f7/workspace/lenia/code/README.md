# 核心实验运行

在工程根目录运行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r code/requirements.txt
.venv/bin/python code/run_core.py --check
.venv/bin/python code/run_core.py --arm spsa --condition 0 --output runs/spsa-c0
.venv/bin/python code/run_core.py --arm run_tumble --condition 0 --output runs/baseline-c0
```

支持 spsa、reward_delta、context_delta、run_tumble、random；条件为0或1。
内容干预使用 `--arm spsa --branch clear` 或 `--branch rotate90`。
种子在 config 中，必要父状态在 initial 中。跨机器使用 run_core.py；保留的原始 run.py 含原站点路径。
这里只保留选定核心方法及对照的复现入口，不覆盖整篇补充材料的所有历史探索。安装依赖和产生新实验输出另占空间。
