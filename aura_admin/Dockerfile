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
ARG NEXT_BASE_PATH=/console
ARG NEXT_PUBLIC_BASE_PATH=/console
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
ENV NEXT_BASE_PATH=/console
ENV NEXT_PUBLIC_BASE_PATH=/console
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
