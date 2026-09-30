CREATE TABLE IF NOT EXISTS catalog_books (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    cover_key VARCHAR(512) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    INDEX catalog_books_order (sort_order, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_works (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    book_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    cover_key VARCHAR(512) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE KEY catalog_work_book (id, book_id),
    INDEX catalog_works_order (book_id, sort_order, id),
    CONSTRAINT catalog_works_book_fk FOREIGN KEY (book_id) REFERENCES catalog_books(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_chapters (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    book_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    work_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    title VARCHAR(200) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    INDEX catalog_chapters_order (book_id, work_id, sort_order, id),
    CONSTRAINT catalog_chapters_book_fk FOREIGN KEY (book_id) REFERENCES catalog_books(id),
    CONSTRAINT catalog_chapters_work_fk FOREIGN KEY (work_id, book_id) REFERENCES catalog_works(id, book_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_bites (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    chapter_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(200) NOT NULL,
    original MEDIUMTEXT NOT NULL,
    translation MEDIUMTEXT NOT NULL,
    commentary MEDIUMTEXT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    INDEX catalog_bites_order (chapter_id, sort_order, id),
    CONSTRAINT catalog_bites_chapter_fk FOREIGN KEY (chapter_id) REFERENCES catalog_chapters(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
