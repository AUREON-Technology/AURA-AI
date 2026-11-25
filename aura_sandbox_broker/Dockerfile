# The sandbox broker. It holds the Railway token, so the image carries nothing but the compiled
# service, its one runtime dependency and the guest scripts, and runs unprivileged.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY --from=build /app/dist/src ./dist/src
COPY guest ./guest
USER node
EXPOSE 8080
CMD ["node", "dist/src/main.js"]
