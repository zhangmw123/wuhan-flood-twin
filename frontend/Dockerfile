FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --registry=https://registry.npmmirror.com --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
