package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

const (
	maxChunkBytes = 5 * 1024 * 1024
	maxBodyBytes  = 6 * 1024 * 1024
	tempRoot      = "/tmp"
)

type Server struct {
	db          *pgxpool.Pool
	redis       *redis.Client
	djangoURL   string
	celeryQueue string
}

type device struct {
	ID     int64
	UserID int64
	Key    string
}

type batch struct {
	ID int64
}

func main() {
	ctx := context.Background()

	db, err := connectDB(ctx)
	if err != nil {
		log.Fatalf("postgres: %v", err)
	}
	defer db.Close()

	rdb := redis.NewClient(&redis.Options{
		Addr:     env("REDIS_ADDR", "redis:6379"),
		Password: os.Getenv("REDIS_PASSWORD"),
		DB:       envInt("REDIS_DB", 0),
	})
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("redis: %v", err)
	}

	s := &Server{
		db:          db,
		redis:       rdb,
		djangoURL:   strings.TrimRight(env("DJANGO_INTERNAL_URL", "http://web:8000"), "/"),
		celeryQueue: env("CELERY_QUEUE", "celery"),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.healthz)
	mux.HandleFunc("/api/v1/sessions/", s.sessionRouter)
	mux.HandleFunc("/api/v1/latest-analysis/", s.latestAnalysis)

	server := &http.Server{
		Addr:              env("LISTEN_ADDR", ":8080"),
		Handler:           loggingMiddleware(recoverMiddleware(mux)),
		ReadTimeout:       15 * time.Second,
		ReadHeaderTimeout: 5 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    32 * 1024,
	}

	log.Printf("audoack gateway listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}

func connectDB(ctx context.Context) (*pgxpool.Pool, error) {
	host := env("DB_HOST", "db")
	port := env("DB_PORT", "5432")
	user := os.Getenv("DB_USER")
	password := os.Getenv("DB_PASSWORD")
	name := os.Getenv("DB_NAME")
	if user == "" || name == "" {
		return nil, errors.New("DB_USER and DB_NAME are required")
	}

	dsn := fmt.Sprintf(
		"postgres://%s:%s@%s:%s/%s?sslmode=%s",
		urlEscape(user), urlEscape(password), host, port, urlEscape(name),
		env("DB_SSLMODE", "disable"),
	)

	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, err
	}
	cfg.MaxConns = int32(envInt("DB_MAX_CONNS", 20))
	cfg.MinConns = int32(envInt("DB_MIN_CONNS", 2))
	cfg.MaxConnLifetime = 30 * time.Minute
	cfg.MaxConnIdleTime = 5 * time.Minute

	var pool *pgxpool.Pool
	for i := 0; i < 30; i++ {
		pool, err = pgxpool.NewWithConfig(ctx, cfg)
		if err == nil {
			if err = pool.Ping(ctx); err == nil {
				return pool, nil
			}
			pool.Close()
		}
		time.Sleep(2 * time.Second)
	}
	return nil, fmt.Errorf("database unavailable: %w", err)
}

func (s *Server) healthz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	if err := s.db.Ping(ctx); err != nil {
		http.Error(w, `{"status":"unhealthy","dependency":"postgres"}`, http.StatusServiceUnavailable)
		return
	}
	if err := s.redis.Ping(ctx).Err(); err != nil {
		http.Error(w, `{"status":"unhealthy","dependency":"redis"}`, http.StatusServiceUnavailable)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) sessionRouter(w http.ResponseWriter, r *http.Request) {
	// Browser long-recorder uses Django session authentication. Keep those
	// requests on the canonical Django api_v1 implementation.
	if !hasDeviceToken(r) {
		s.proxyToDjango(w, r)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/v1/sessions/")
	parts := strings.Split(strings.Trim(path, "/"), "/")

	switch {
	case len(parts) == 0 && r.Method == http.MethodPost:
		s.createSession(w, r)
	case len(parts) == 2 && parts[1] == "chunks" && r.Method == http.MethodPost:
		id, err := strconv.ParseInt(parts[0], 10, 64)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid batch_id")
			return
		}
		s.uploadChunk(w, r, id)
	case len(parts) == 2 && parts[1] == "finalize" && r.Method == http.MethodPost:
		id, err := strconv.ParseInt(parts[0], 10, 64)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid batch_id")
			return
		}
		s.finalizeSession(w, r, id)
	case len(parts) == 2 && parts[1] == "heartbeat" && r.Method == http.MethodPost:
		id, err := strconv.ParseInt(parts[0], 10, 64)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid batch_id")
			return
		}
		s.heartbeat(w, r, id)
	default:
		s.proxyToDjango(w, r)
	}
}

func (s *Server) authenticateDevice(ctx context.Context, r *http.Request) (*device, error) {
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	scheme, key, ok := strings.Cut(auth, " ")
	if !ok || !strings.EqualFold(scheme, "Token") || strings.TrimSpace(key) == "" {
		return nil, errors.New("missing or invalid device token")
	}
	key = strings.TrimSpace(key)

	var d device
	err := s.db.QueryRow(ctx,
		`SELECT id, user_id, key FROM audio_analytics_device WHERE key = $1`,
		key,
	).Scan(&d.ID, &d.UserID, &d.Key)
	if err != nil {
		return nil, errors.New("invalid device token")
	}
	return &d, nil
}

func (s *Server) touchDevice(ctx context.Context, id int64) {
	_, _ = s.db.Exec(ctx,
		`UPDATE audio_analytics_device SET last_seen = NOW() WHERE id = $1`,
		id,
	)
}

func (s *Server) createSession(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	d, err := s.authenticateDevice(ctx, r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	s.touchDevice(ctx, d.ID)

	title := "Long Live Recording"
	if r.Header.Get("Content-Type") == "application/json" {
		var payload struct {
			Title string `json:"title"`
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 64*1024)).Decode(&payload); err == nil && payload.Title != "" {
			title = payload.Title
		}
	}

	var id int64
	err = s.db.QueryRow(ctx, `
		INSERT INTO audio_analytics_batchupload
			(user_id, zip_file, uploaded_at, status, error_message, metrics_json, name, device_id)
		VALUES ($1, '', NOW(), 'recording', NULL, NULL, $2, $3)
		RETURNING id
	`, d.UserID, truncate(title+" (Live Stream)", 255), d.ID).Scan(&id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create recording session")
		return
	}

	writeJSON(w, http.StatusCreated, map[string]any{
		"status":   "success",
		"batch_id": id,
	})
}

func (s *Server) uploadChunk(w http.ResponseWriter, r *http.Request, batchID int64) {
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	d, err := s.authenticateDevice(ctx, r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	s.touchDevice(ctx, d.ID)

	if r.ContentLength > maxBodyBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "audio chunk exceeds 5 MB")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)

	if err := r.ParseMultipartForm(maxBodyBytes); err != nil {
		writeError(w, http.StatusBadRequest, "invalid multipart audio upload")
		return
	}

	indexStr := r.FormValue("index")
	audio, header, err := r.FormFile("chunk_data")
	if err != nil {
		writeError(w, http.StatusBadRequest, "missing index or chunk_data")
		return
	}
	defer audio.Close()

	index, err := strconv.Atoi(indexStr)
	if err != nil || index < 0 {
		writeError(w, http.StatusBadRequest, "index must be a non-negative integer")
		return
	}
	if header.Size > maxChunkBytes {
		writeError(w, http.StatusBadRequest, "audio chunk exceeds 5 MB")
		return
	}

	var status string
	err = s.db.QueryRow(ctx, `
		SELECT status FROM audio_analytics_batchupload
		WHERE id = $1 AND user_id = $2 AND device_id = $3
	`, batchID, d.UserID, d.ID).Scan(&status)
	if err != nil || status != "recording" {
		writeError(w, http.StatusNotFound, "session not found or already finalized")
		return
	}

	filename := fmt.Sprintf("chunk_%04d.wav", index)

	var existingID int64
	var existingAudio string
	err = s.db.QueryRow(ctx, `
		SELECT id, COALESCE(audio_file, '')
		FROM audio_analytics_audioanalysis
		WHERE batch_id = $1 AND filename = $2
		LIMIT 1
	`, batchID, filename).Scan(&existingID, &existingAudio)
	if err == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":     "chunk_saved",
			"filename":   filename,
			"index":      index,
			"duplicate":  true,
			"processing": existingAudio == "",
		})
		return
	}

	// Convert directly into the shared /tmp volume. No audio bytes are sent
	// through Redis or PostgreSQL.
	dir := filepath.Join(tempRoot, fmt.Sprintf("batch_stream_%d", batchID))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create audio workspace")
		return
	}
	rawPath := filepath.Join(dir, fmt.Sprintf("temp_%d_%s.webm", index, randomHex(8)))
	wavPath := filepath.Join(dir, filename)

	raw, err := os.Create(rawPath)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create temporary audio file")
		return
	}
	if _, err = io.Copy(raw, io.LimitReader(audio, maxChunkBytes+1)); err != nil {
		raw.Close()
		os.Remove(rawPath)
		writeError(w, http.StatusBadRequest, "failed to read audio chunk")
		return
	}
	raw.Close()

	if err := convertToWAV(r.Context(), rawPath, wavPath); err != nil {
		os.Remove(rawPath)
		os.Remove(wavPath)
		log.Printf("ffmpeg conversion failed batch=%d index=%d: %v", batchID, index, err)
		writeError(w, http.StatusBadRequest, "audio conversion failed")
		return
	}
	os.Remove(rawPath)

	tx, err := s.db.Begin(ctx)
	if err != nil {
		os.Remove(wavPath)
		writeError(w, http.StatusInternalServerError, "database transaction failed")
		return
	}
	defer tx.Rollback(ctx)

	// Lock the batch so two concurrent uploads cannot reserve the same index.
	var lockedStatus string
	if err := tx.QueryRow(ctx,
		`SELECT status FROM audio_analytics_batchupload WHERE id = $1 AND user_id = $2 AND device_id = $3 FOR UPDATE`,
		batchID, d.UserID, d.ID,
	).Scan(&lockedStatus); err != nil || lockedStatus != "recording" {
		os.Remove(wavPath)
		writeError(w, http.StatusNotFound, "session not found or already finalized")
		return
	}

	err = tx.QueryRow(ctx, `
		SELECT id FROM audio_analytics_audioanalysis
		WHERE batch_id = $1 AND filename = $2 LIMIT 1
	`, batchID, filename).Scan(&existingID)
	if err == nil {
		tx.Rollback(ctx)
		os.Remove(wavPath)
		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "chunk_queued",
			"filename":  filename,
			"index":     index,
			"duplicate": true,
		})
		return
	}

	if err := tx.QueryRow(ctx, `
		INSERT INTO audio_analytics_audioanalysis
			(batch_id, filename, status, error_details, background_noise_present, speaker_overlap_present,
			 long_silence_present, created_at)
		VALUES ($1, $2, 'pending', '', FALSE, FALSE, FALSE, NOW())
		RETURNING id
	`, batchID, filename).Scan(&existingID); err != nil {
		os.Remove(wavPath)
		writeError(w, http.StatusInternalServerError, "failed to reserve audio chunk")
		return
	}

	if err := tx.Commit(ctx); err != nil {
		os.Remove(wavPath)
		writeError(w, http.StatusInternalServerError, "failed to commit audio chunk")
		return
	}

	if err := s.enqueueCeleryTask(r.Context(), "audio_analytics.tasks.process_audio_chunk_task",
		[]any{batchID, wavPath, filename}); err != nil {
		log.Printf("celery enqueue failed batch=%d index=%d: %v", batchID, index, err)
		// Keep the file and pending DB row. A later operational retry can
		// safely enqueue the task without losing the only audio copy.
		writeError(w, http.StatusServiceUnavailable, "audio queued locally but worker queue is unavailable")
		return
	}

	writeJSON(w, http.StatusAccepted, map[string]any{
		"status":     "chunk_queued",
		"filename":   filename,
		"index":      index,
		"processing": true,
	})
}

func (s *Server) finalizeSession(w http.ResponseWriter, r *http.Request, batchID int64) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	d, err := s.authenticateDevice(ctx, r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	s.touchDevice(ctx, d.ID)

	tag, err := s.db.Exec(ctx, `
		UPDATE audio_analytics_batchupload
		SET status = 'completed'
		WHERE id = $1 AND user_id = $2 AND device_id = $3 AND status = 'recording'
	`, batchID, d.UserID, d.ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to finalize recording")
		return
	}
	if tag.RowsAffected() == 0 {
		writeError(w, http.StatusNotFound, "session not found or already finalized")
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "completed",
		"batch_id":     batchID,
		"redirect_url": fmt.Sprintf("/batches/%d/", batchID),
	})
}

func (s *Server) heartbeat(w http.ResponseWriter, r *http.Request, batchID int64) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	d, err := s.authenticateDevice(ctx, r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	s.touchDevice(ctx, d.ID)

	var status string
	err = s.db.QueryRow(ctx, `
		SELECT status FROM audio_analytics_batchupload
		WHERE id = $1 AND user_id = $2 AND device_id = $3
	`, batchID, d.UserID, d.ID).Scan(&status)
	if err != nil {
		writeError(w, http.StatusNotFound, "session not found or already finalized")
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "alive",
		"batch_status": status,
		"device_id":    d.ID,
	})
}

func (s *Server) latestAnalysis(w http.ResponseWriter, r *http.Request) {
	if !hasDeviceToken(r) {
		s.proxyToDjango(w, r)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	d, err := s.authenticateDevice(ctx, r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}
	s.touchDevice(ctx, d.ID)

	var bID int64
	var bStatus string
	err = s.db.QueryRow(ctx, `
		SELECT id, status FROM audio_analytics_batchupload
		WHERE user_id = $1 AND device_id = $2
		ORDER BY id DESC LIMIT 1
	`, d.UserID, d.ID).Scan(&bID, &bStatus)

	if errors.Is(err, context.Canceled) {
		return
	}
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":       "no_recording",
			"batch_id":     nil,
			"batch_status": nil,
			"analysis":     nil,
		})
		return
	}

	var a struct {
		ID                      int64
		Filename                string
		Status                  string
		EmotionalTone           *string
		EmotionalIntensity      *string
		BackgroundNoisePresent  bool
		BackgroundNoiseType     string
		BackgroundNoiseSeverity string
		AudioQuality            *string
		SpeakerOverlapPresent   bool
		LongSilencePresent      bool
		Confidence              *float64
		CreatedAt               time.Time
	}
	err = s.db.QueryRow(ctx, `
		SELECT id, filename, status, emotional_tone, emotional_intensity,
		       background_noise_present, COALESCE(background_noise_type, ''),
		       background_noise_severity, audio_quality, speaker_overlap_present,
		       long_silence_present, confidence, created_at
		FROM audio_analytics_audioanalysis
		WHERE batch_id = $1 AND status = 'success'
		ORDER BY id DESC LIMIT 1
	`, bID).Scan(
		&a.ID, &a.Filename, &a.Status, &a.EmotionalTone, &a.EmotionalIntensity,
		&a.BackgroundNoisePresent, &a.BackgroundNoiseType, &a.BackgroundNoiseSeverity,
		&a.AudioQuality, &a.SpeakerOverlapPresent, &a.LongSilencePresent,
		&a.Confidence, &a.CreatedAt,
	)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":       "processing",
			"batch_id":     bID,
			"batch_status": bStatus,
			"analysis":     nil,
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "success",
		"batch_id":     bID,
		"batch_status": bStatus,
		"analysis": map[string]any{
			"id":                        a.ID,
			"batch_id":                  bID,
			"filename":                  a.Filename,
			"status":                    a.Status,
			"created_at":                a.CreatedAt.Format(time.RFC3339Nano),
			"emotional_tone":            a.EmotionalTone,
			"emotional_intensity":       a.EmotionalIntensity,
			"background_noise_present":  a.BackgroundNoisePresent,
			"background_noise_type":     a.BackgroundNoiseType,
			"background_noise_severity": a.BackgroundNoiseSeverity,
			"audio_quality":             a.AudioQuality,
			"speaker_overlap_present":   a.SpeakerOverlapPresent,
			"long_silence_present":      a.LongSilencePresent,
			"confidence":                a.Confidence,
		},
	})
}

func (s *Server) enqueueCeleryTask(ctx context.Context, task string, args []any) error {
	id := uuid4()
	bodyJSON, err := json.Marshal([]any{args, map[string]any{}, map[string]any{
		"callbacks": nil,
		"errbacks":  nil,
		"chain":     nil,
		"chord":     nil,
	}})
	if err != nil {
		return err
	}

	message := map[string]any{
		"body":             base64.StdEncoding.EncodeToString(bodyJSON),
		"content-encoding": "utf-8",
		"content-type":     "application/json",
		"headers": map[string]any{
			"lang":       "py",
			"task":       task,
			"id":         id,
			"root_id":    id,
			"parent_id":  nil,
			"argsrepr":   reprArgs(args),
			"kwargsrepr": "{}",
			"origin":     "audoack-gateway",
		},
		"properties": map[string]any{
			"correlation_id": id,
			"reply_to":       "",
			"delivery_mode":  2,
			"delivery_info": map[string]any{
				"exchange":    "",
				"routing_key": s.celeryQueue,
			},
			"priority":      0,
			"body_encoding": "base64",
		},
	}

	payload, err := json.Marshal(message)
	if err != nil {
		return err
	}

	return s.redis.LPush(ctx, s.celeryQueue, payload).Err()
}

func (s *Server) proxyToDjango(w http.ResponseWriter, r *http.Request) {
	target := s.djangoURL + r.URL.RequestURI()
	req, err := http.NewRequestWithContext(r.Context(), r.Method, target, r.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "failed to proxy request")
		return
	}
	req.Header = r.Header.Clone()
	req.Header.Set("X-Forwarded-For", clientIP(r))
	req.Header.Set("X-Forwarded-Proto", "https")
	req.Host = r.Host

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeError(w, http.StatusBadGateway, "upstream unavailable")
		return
	}
	defer resp.Body.Close()

	for k, values := range resp.Header {
		for _, value := range values {
			w.Header().Add(k, value)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

func convertToWAV(ctx context.Context, rawPath, wavPath string) error {
	cmd := exec.CommandContext(ctx, "ffmpeg",
		"-hide_banner", "-loglevel", "error",
		"-y", "-i", rawPath,
		"-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
		wavPath,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%w: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func hasDeviceToken(r *http.Request) bool {
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	scheme, key, ok := strings.Cut(auth, " ")
	return ok && strings.EqualFold(scheme, "Token") && strings.TrimSpace(key) != ""
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, code int, message string) {
	writeJSON(w, code, map[string]string{"error": message})
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func uuid4() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

func reprArgs(args []any) string {
	data, _ := json.Marshal(args)
	return string(data)
}

func truncate(s string, max int) string {
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max])
}

func urlEscape(s string) string {
	var b bytes.Buffer
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') || strings.ContainsRune("-_.~", rune(c)) {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	return r.RemoteAddr
}

func loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
	})
}

func recoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if v := recover(); v != nil {
				log.Printf("panic: %v", v)
				writeError(w, http.StatusInternalServerError, "internal server error")
			}
		}()
		next.ServeHTTP(w, r)
	})
}
