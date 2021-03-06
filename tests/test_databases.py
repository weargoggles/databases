import asyncio
import datetime
import functools
import os

import pytest
import sqlalchemy

from databases import Database, DatabaseURL

assert "TEST_DATABASE_URLS" in os.environ, "TEST_DATABASE_URLS is not set."

DATABASE_URLS = [url.strip() for url in os.environ["TEST_DATABASE_URLS"].split(",")]


class MyEpochType(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.Integer

    epoch = datetime.date(1970, 1, 1)

    def process_bind_param(self, value, dialect):
        return (value - self.epoch).days

    def process_result_value(self, value, dialect):
        return self.epoch + datetime.timedelta(days=value)


metadata = sqlalchemy.MetaData()

notes = sqlalchemy.Table(
    "notes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("text", sqlalchemy.String(length=100)),
    sqlalchemy.Column("completed", sqlalchemy.Boolean),
)

# Used to test DateTime
articles = sqlalchemy.Table(
    "articles",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("title", sqlalchemy.String(length=100)),
    sqlalchemy.Column("published", sqlalchemy.DateTime),
)

# Used to test JSON
session = sqlalchemy.Table(
    "session",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("data", sqlalchemy.JSON),
)

# Used to test custom column types
custom_date = sqlalchemy.Table(
    "custom_date",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("title", sqlalchemy.String(length=100)),
    sqlalchemy.Column("published", MyEpochType),
)


@pytest.fixture(autouse=True, scope="module")
def create_test_database():
    # Create test databases
    for url in DATABASE_URLS:
        database_url = DatabaseURL(url)
        if database_url.dialect == "mysql":
            url = str(database_url.replace(driver="pymysql"))
        engine = sqlalchemy.create_engine(url)
        metadata.create_all(engine)

    # Run the test suite
    yield

    # Drop test databases
    for url in DATABASE_URLS:
        database_url = DatabaseURL(url)
        if database_url.dialect == "mysql":
            url = str(database_url.replace(driver="pymysql"))
        engine = sqlalchemy.create_engine(url)
        metadata.drop_all(engine)


def async_adapter(wrapped_func):
    """
    Decorator used to run async test cases.
    """

    @functools.wraps(wrapped_func)
    def run_sync(*args, **kwargs):
        loop = asyncio.get_event_loop()
        task = wrapped_func(*args, **kwargs)
        return loop.run_until_complete(task)

    return run_sync


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_queries(database_url):
    """
    Test that the basic `execute()`, `execute_many()`, `fetch_all()``, and
    `fetch_one()` interfaces are all supported.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            # execute()
            query = notes.insert()
            values = {"text": "example1", "completed": True}
            await database.execute(query, values)

            # execute_many()
            query = notes.insert()
            values = [
                {"text": "example2", "completed": False},
                {"text": "example3", "completed": True},
            ]
            await database.execute_many(query, values)

            # fetch_all()
            query = notes.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 3
            assert results[0]["text"] == "example1"
            assert results[0]["completed"] == True
            assert results[1]["text"] == "example2"
            assert results[1]["completed"] == False
            assert results[2]["text"] == "example3"
            assert results[2]["completed"] == True

            # fetch_one()
            query = notes.select()
            result = await database.fetch_one(query=query)
            assert result["text"] == "example1"
            assert result["completed"] == True


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_rollback_isolation(database_url):
    """
    Ensure that `database.transaction(force_rollback=True)` provides strict isolation.
    """

    async with Database(database_url) as database:
        # Perform some INSERT operations on the database.
        async with database.transaction(force_rollback=True):
            query = notes.insert().values(text="example1", completed=True)
            await database.execute(query)

        # Ensure INSERT operations have been rolled back.
        query = notes.select()
        results = await database.fetch_all(query=query)
        assert len(results) == 0


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_transaction_commit(database_url):
    """
    Ensure that transaction commit is supported.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            async with database.transaction():
                query = notes.insert().values(text="example1", completed=True)
                await database.execute(query)

            query = notes.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 1


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_transaction_rollback(database_url):
    """
    Ensure that transaction rollback is supported.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            try:
                async with database.transaction():
                    query = notes.insert().values(text="example1", completed=True)
                    await database.execute(query)
                    raise RuntimeError()
            except RuntimeError:
                pass

            query = notes.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 0


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_transaction_commit_low_level(database_url):
    """
    Ensure that an explicit `await transaction.commit()` is supported.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            transaction = await database.transaction()
            try:
                query = notes.insert().values(text="example1", completed=True)
                await database.execute(query)
            except:  # pragma: no cover
                await transaction.rollback()
            else:
                await transaction.commit()

            query = notes.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 1


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_transaction_rollback_low_level(database_url):
    """
    Ensure that an explicit `await transaction.rollback()` is supported.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            transaction = await database.transaction()
            try:
                query = notes.insert().values(text="example1", completed=True)
                await database.execute(query)
                raise RuntimeError()
            except:
                await transaction.rollback()
            else:  # pragma: no cover
                await transaction.commit()

            query = notes.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 0


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_datetime_field(database_url):
    """
    Test DataTime columns, to ensure records are coerced to/from proper Python types.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            now = datetime.datetime.now().replace(microsecond=0)

            # execute()
            query = articles.insert()
            values = {"title": "Hello, world", "published": now}
            await database.execute(query, values)

            # fetch_all()
            query = articles.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 1
            assert results[0]["title"] == "Hello, world"
            assert results[0]["published"] == now


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_json_field(database_url):
    """
    Test JSON columns, to ensure correct cross-database support.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            # execute()
            query = session.insert()
            values = {"data": {"text": "hello", "boolean": True, "int": 1}}
            await database.execute(query, values)

            # fetch_all()
            query = session.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 1
            assert results[0]["data"] == {"text": "hello", "boolean": True, "int": 1}


@pytest.mark.parametrize("database_url", DATABASE_URLS)
@async_adapter
async def test_custom_field(database_url):
    """
    Test custom column types.
    """

    async with Database(database_url) as database:
        async with database.transaction(force_rollback=True):
            today = datetime.date.today()

            # execute()
            query = custom_date.insert()
            values = {"title": "Hello, world", "published": today}
            await database.execute(query, values)

            # fetch_all()
            query = custom_date.select()
            results = await database.fetch_all(query=query)
            assert len(results) == 1
            assert results[0]["title"] == "Hello, world"
            assert results[0]["published"] == today
