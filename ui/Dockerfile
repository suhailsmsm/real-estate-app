# Build context is the REPO ROOT (see docker-compose.yml), matching every
# other service's convention (CLAUDE.md "Two Docker build contexts are the
# repo root") even though this image needs nothing outside ui/ — no
# packages/dxb-core, no other package. Consistency over a marginally smaller
# context.
#
# Multi-stage: build the SPA with Node, then hand the static output to a tiny
# nginx that does nothing but serve it. Node, npm, and node_modules never
# ship in the final image — only the ~2MB of built assets do. This is also
# what makes one image right for both jobs asked for: "local testing and
# development" (docker compose up ui, or the whole stack) and later cloud
# deployment — it is a self-contained, ordinary static-file server container,
# portable to whatever runs a Docker image.
#
# This does NOT replace `npm run dev` (Vite's dev server, hot module reload)
# for active coding — see ui/README.md. It is the containerized, built-for-
# real form of the app: what a deploy actually ships.

FROM node:22-alpine AS build
WORKDIR /app
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
