# TEKIJIN frontend image — Next.js (App Router), production build.
#
# NEXT_PUBLIC_API_BASE_URL is baked in at BUILD time (Next.js inlines
# NEXT_PUBLIC_* vars into the client bundle) — it must be the URL the user's
# own browser can reach, i.e. the backend's published HOST port, never the
# in-network service name.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.mjs ./

EXPOSE 3000

CMD ["npm", "run", "start"]
