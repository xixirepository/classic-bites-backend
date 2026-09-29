-- Additive authentication schema. Never modifies existing service tables.
CREATE TABLE IF NOT EXISTS auth_users (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NULL,
    google_sub VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY auth_users_email_unique (email),
    UNIQUE KEY auth_users_google_sub_unique (google_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    access_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    access_expires_at BIGINT UNSIGNED NOT NULL,
    refresh_expires_at BIGINT UNSIGNED NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    revoked_at BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY auth_sessions_access_hash_unique (access_hash),
    KEY auth_sessions_user_id (user_id),
    KEY auth_sessions_expiry (refresh_expires_at),
    CONSTRAINT auth_sessions_user_fk FOREIGN KEY (user_id) REFERENCES auth_users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    used_at BIGINT UNSIGNED NULL,
    PRIMARY KEY (token_hash),
    KEY auth_refresh_tokens_session_id (session_id),
    CONSTRAINT auth_refresh_tokens_session_fk FOREIGN KEY (session_id) REFERENCES auth_sessions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    rate_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    window_started_at BIGINT UNSIGNED NOT NULL,
    request_count INT UNSIGNED NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (rate_key),
    KEY auth_rate_limits_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
