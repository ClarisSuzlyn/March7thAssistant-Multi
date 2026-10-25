# [MULTI_ACCOUNT] 多账号扩展包。
#
# 这里只放「新增」代码，不修改原项目模块，便于后续跟随上游更新：
#   account_manager.py  账号清单 + 每账号配置隔离（cfg.config 内存替换）
#   logout.py           图像/OCR 自动登出
#   scheduler.py        多账号轮询调度（登录 → 任务 → 登出 → 切号）
