# Trending Data Fetcher - Python Version

This is a Python equivalent of the Node.js trending data fetcher. It scrapes trending data from multiple sources and stores it in a PostgreSQL database.

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://user:password@host:port/database
```

### 3. Run the Fetcher

```bash
python index.py
```

## Features

- **GitHub Trending**: Scrapes top weekly trending repositories
- **Hacker News**: Fetches top 25 stories
- **Dev.to**: Gets trending tech articles
- **Product Hunt**: Scrapes product launches
- **Database**: Stores all data in PostgreSQL with proper indexing
- **Cleanup**: Automatically removes records older than 7 days
- **Summary**: Prints database statistics after run

## Dependencies

- `playwright>=1.40.0` - Browser automation
- `psycopg2-binary>=2.9.9` - PostgreSQL adapter
- `python-dotenv>=1.0.0` - Environment variable management

## Key Differences from Node.js Version

| Feature       | JavaScript       | Python           |
| ------------- | ---------------- | ---------------- |
| Async         | async/await      | asyncio          |
| Browser       | Playwright (npm) | Playwright (pip) |
| Database      | pg pool          | psycopg2 pool    |
| Env vars      | dotenv           | python-dotenv    |
| HTTP requests | Page navigation  | Page navigation  |

## Performance

- Total execution time: ~60-70 seconds
- Fetches ~60-70 records total
- Supports concurrent requests with proper rate limiting

## Database Schema

```sql
CREATE TABLE trending_data (
    id          SERIAL PRIMARY KEY,
    source      TEXT        NOT NULL,
    category    TEXT        NOT NULL,
    title       TEXT        NOT NULL,
    url         TEXT,
    summary     TEXT,
    tags        TEXT[]      DEFAULT '{}',
    score       INTEGER     DEFAULT 0,
    fetched_at  TIMESTAMPTZ DEFAULT NOW()
);
```

## Troubleshooting

**ECONNREFUSED error**: Make sure your PostgreSQL connection string is correct in `.env`

**Timeout errors**: Increase timeout values in scraper functions or check internet connection

**Bot detection**: The code includes anti-bot measures (user agent spoofing, webdriver flag removal, etc.)
