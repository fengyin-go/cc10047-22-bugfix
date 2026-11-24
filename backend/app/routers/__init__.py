"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import chains, governance, resource

# governance 的固定路径必须先于 resource 的 /api/{module}/{entry_id} 注册，
# 否则 /api/governance/... 会被当成某个模块的条目查询。
ROUTERS = [governance, chains, resource]
