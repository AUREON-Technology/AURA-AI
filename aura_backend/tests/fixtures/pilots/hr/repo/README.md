# HR pilot application (fixture)

The application code behind the HR pilot database, as a Git repository would hold it: SQLAlchemy
models, Alembic migrations and a FastAPI router. Read by the AP10 Git connector's goldens; never
imported or run. `tests/` holds a fixture model (`scratch_models.py`, named so no test runner collects it) that discovery must ignore.
Migration files are named out of revision order on purpose: `0000_add_salary.py` depends on `0001_initial.py` through `down_revision`.
