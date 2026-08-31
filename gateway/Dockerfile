FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/audoack-gateway .

FROM alpine:3.21
RUN apk add --no-cache ffmpeg ca-certificates tzdata
RUN addgroup -S app && adduser -S -G app app
COPY --from=build /out/audoack-gateway /usr/local/bin/audoack-gateway
RUN mkdir -p /tmp && chown app:app /tmp
USER app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/audoack-gateway"]
