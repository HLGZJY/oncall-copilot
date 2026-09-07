# oncall ingest 服务镜像（M1 issue 06：Alertmanager receiver 目标收编进 compose）
#
# 依赖随镜像安装（pyproject 锁版本区间，requires-python >=3.11,<3.12 对齐 C1）；
# 源码由 compose volume 直挂 /app/src 遮蔽镜像内副本——改代码只需
# docker-compose restart oncall，无需重建镜像。
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

ENV PYTHONPATH=/app/src
EXPOSE 8010

CMD ["uvicorn", "oncall.ingest.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
