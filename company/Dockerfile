FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY blueprint.py INSTALL_AI.txt ./
COPY blueprint_folders/ ./blueprint_folders/
COPY blueprint_library/ ./blueprint_library/
COPY library_web/ ./library_web/
COPY assets/icons/ ./assets/icons/
COPY integrations/mcp/ ./integrations/mcp/
COPY tools/upgrade_workspace.py tools/check_installation.py ./tools/
COPY company/entrypoint.py ./company/entrypoint.py
RUN mkdir -p /data && chmod -R a+rX /app && chown -R 10001:10001 /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8787
CMD ["python", "company/entrypoint.py"]
