FROM node:22-alpine AS deps
WORKDIR /app
# The lockfile pins what `npm audit` checked; `npm install` without it re-resolved every range at
# build time, so the deployed tree could differ from the audited one.
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Empty serves the console from the root of its own host (Railway: console.aureon-ai.tech). The
# VPS compose files pass /console, where nginx shares one host. Both values are baked in at build
# time: Next inlines NEXT_PUBLIC_* and the standalone server embeds basePath.
ARG NEXT_BASE_PATH=
ARG NEXT_PUBLIC_BASE_PATH=
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID
ARG NEXT_PUBLIC_GRAFANA_EMBED=1
ENV NEXT_BASE_PATH=$NEXT_BASE_PATH
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NEXT_PUBLIC_GRAFANA_EMBED=$NEXT_PUBLIC_GRAFANA_EMBED
RUN npm run build

# Runner expects Next `output: "standalone"` (see next.config.ts). That emits
# .next/standalone (server.js + traced deps); static assets still copy separately.
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Same values the build used, so the runtime env never contradicts what was baked in.
ARG NEXT_BASE_PATH=
ARG NEXT_PUBLIC_BASE_PATH=
ENV NEXT_BASE_PATH=$NEXT_BASE_PATH
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
