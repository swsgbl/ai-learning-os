FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# M9-07: API 地址构建期注入（NEXT_PUBLIC_* 只能在 build 阶段生效）
ARG NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
# M14-159: 公共 Web base path 构建期注入（默认空 = 根路径构建不变；
# 公共 Beta 构建传 /aios —— 经 next.config.ts 结构化校验 fail-closed）
ARG NEXT_PUBLIC_BASE_PATH=
ENV NEXT_PUBLIC_BASE_PATH=${NEXT_PUBLIC_BASE_PATH}
COPY --from=deps /app ./
COPY apps/web ./apps/web
RUN npm run build --workspace apps/web

FROM node:22-alpine AS run
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/apps/web/.next/standalone ./
COPY --from=build /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /app/apps/web/public ./apps/web/public
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
