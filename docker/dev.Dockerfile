# Optional development image for the Vite frontend. Backend development remains
# in the backend/ project and is proxied by vite.config.ts.
FROM node:22-alpine
WORKDIR /workspace/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
