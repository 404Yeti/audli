FROM node:22-slim
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
ENV BACKEND_URL=http://api:8000
RUN npm run build
CMD ["npm", "start"]
