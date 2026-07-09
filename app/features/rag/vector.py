from sqlalchemy.types import UserDefinedType


class Vector768(UserDefinedType):
    """Postgres pgvector column type locked to 768 dimensions."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector(768)"
