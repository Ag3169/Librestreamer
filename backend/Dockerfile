FROM golang:1.26-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /server ./cmd/server

FROM alpine:3.20
RUN apk add --no-cache ffmpeg nvidia-smi 2>/dev/null || true
COPY --from=builder /server /server
EXPOSE 8080 9090
VOLUME ["/data", "/media"]
ENTRYPOINT ["/server", "-config", "config.json"]
