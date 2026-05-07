import os, time
import psycopg2
from urllib.parse import urlparse
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec
from google import genai
from google.genai import errors
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

load_dotenv()

# ------ CONFIG ------
INDEX_NAME = "rag-index"
# ---------------------

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])

# 📌 Connect to PostgreSQL and fetch trending data
def get_db_connection():
    """Parse DATABASE_URL and create PostgreSQL connection"""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    
    # Parse PostgreSQL connection string
    parsed = urlparse(database_url)
    conn = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip('/'),
        user=parsed.username,
        password=parsed.password,
        sslmode='require'
    )
    return conn

# 📌 Read from PostgreSQL - combine each row into one rich text chunk
def read_postgres():
    """Fetch all trending data from PostgreSQL and create chunks"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, source, category, title, url, summary, tags, score
                FROM trending_data
                ORDER BY fetched_at DESC
            """)
            rows = cur.fetchall()
        
        docs = []
        for row in rows:
            doc_id, source, category, title, url, summary, tags, score = row
            # Create a rich text chunk from each row
            tags_str = ", ".join(tags) if tags else "none"
            chunk = (
                f"ID: {doc_id} | "
                f"Source: {source} | "
                f"Category: {category} | "
                f"Title: {title} | "
                f"URL: {url or 'N/A'} | "
                f"Summary: {summary or 'N/A'} | "
                f"Tags: {tags_str} | "
                f"Score: {score}"
            )
            docs.append(chunk)
        
        return docs
    finally:
        conn.close()

# 📌 Embed with auto-retry on 429 only
@retry(
    wait=wait_exponential(multiplier=1, min=5, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(errors.ClientError)
)
def embed(text):
    try:
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text
        )
        return result.embeddings[0].values
    except Exception as e:
        print(f"🔴 Real error: {type(e).__name__}: {e}")
        raise

# 📌 Ensure index exists with correct dimension
def ensure_index():
    existing = [i.name for i in pc.list_indexes()]
    if INDEX_NAME in existing:
        info = pc.describe_index(INDEX_NAME)
        if info.dimension != 3072:
            print("🗑️ Deleting old index with wrong dimension...")
            pc.delete_index(INDEX_NAME)
            time.sleep(10)
        else:
            print("✅ Index already exists with correct dimension.")
            return

    print("📌 Creating index...")
    pc.create_index(
        name=INDEX_NAME,
        dimension=3072,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1")
    )
    print("⏳ Waiting for index to be ready...")
    time.sleep(30)

# 📌 Upload
def upload():
    ensure_index()
    index = pc.Index(INDEX_NAME)
    docs = read_postgres()
    print(f"🚀 Uploading {len(docs)} embeddings from PostgreSQL...")

    BATCH_SIZE = 50
    vectors = []

    for i, doc in enumerate(docs):
        try:
            v = embed(doc)
            vectors.append({
                "id": f"doc-{i}",
                "values": v,
                "metadata": {"text": doc}
            })
            time.sleep(1)

            if len(vectors) >= BATCH_SIZE:
                print(f"⏫ Uploading batch up to #{i}")
                index.upsert(vectors=vectors, namespace="default")
                vectors.clear()

        except Exception as e:
            print(f"⚠️ Error on row {i}: {e}")

    if vectors:
        print(f"⏫ Uploading final batch of {len(vectors)}")
        index.upsert(vectors=vectors, namespace="default")

    print("🎉 Done!")

upload()