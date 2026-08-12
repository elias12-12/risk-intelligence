# The service, in a container, so that `docker compose up` is the whole of the
# setup instructions.
#
# Before this file the answer to "how do I run it" was four answers: Docker for
# the database, a host virtualenv for `bootstrap.ps1`, a terminal holding
# `python -m glassbox serve`, and a compose profile for the console. Every one
# of them is a place a demo can fail in front of an audience, and three of them
# needed Python on the machine.
#
# ONE IMAGE, TWO SERVICES. `init` runs the build and exits; `api` serves. They
# are the same image with different commands, because a separate init image
# would be a second definition of the environment the build runs in — and the
# build is the thing whose reproducibility the whole project rests on.
FROM python:3.12-slim-bookworm

# Unbuffered, so `docker compose logs` shows the bootstrap's progress as it
# happens rather than in one block when it finishes. A build that prints nothing
# for ninety seconds looks hung, and looking hung is what makes somebody hit
# Ctrl-C halfway through a migration.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first, by requirements alone, so editing source does not
# reinstall psycopg. `psycopg[binary]` ships wheels — there is no libpq-dev and
# no build toolchain in this image on purpose.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Everything the service and the bootstrap read at runtime. `db/` because
# migrations and seeds are applied from files, `fixtures/` because the generator
# writes into it and `reset_db` loads from it, `contract/` because the schema
# export compares against the committed bytes.
COPY pyproject.toml ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY db/ ./db/
COPY contract/ ./contract/
COPY fixtures/ ./fixtures/

# Editable, so `python -m glassbox` resolves and `console/dist` is still found
# relative to the source tree rather than to a copy in site-packages.
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# `serve` and not the bootstrap: an image whose default command rebuilds a
# database is an image that rebuilds a database when somebody runs it to check a
# version number. The init service asks for the build by name.
CMD ["python", "-m", "glassbox", "serve"]
