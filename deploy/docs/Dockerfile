# The docs site as a static nginx image. Export first, then build from the repository root:
#   bash scripts/export-site.sh
#   docker build -f deploy/docs/Dockerfile -t team-kit-docs .
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY site/dist /usr/share/nginx/html
EXPOSE 8080
