# The console, in a container, so that working on it costs the host nothing.
#
# Node is not a dependency of this project. It is a dependency of ONE directory
# in it, and `bootstrap.ps1` says so in as many words — "Not run here, because
# it needs Node". This image is how that stays true: the packages
# `package-lock.json` pins are installed in a container and a named volume, and
# never on the machine running the browser.
#
# glibc rather than alpine. The lockfile carries every platform variant of
# esbuild and rollup, musl included, so either would resolve — the gnu build is
# simply the one every other platform in that lockfile was verified against.
FROM node:22-bookworm-slim

WORKDIR /app

# Dependencies by lockfile alone, so editing a component does not reinstall.
# `npm ci` and not `npm install`: the lockfile is committed and exact — the
# `//pins` note in package.json says why — and `ci` is the only one of the two
# that refuses to quietly move it.
COPY package.json package-lock.json ./
RUN npm ci

# The stamp the entrypoint compares against, written at build time so that a
# container whose node_modules volume was just seeded from this image does not
# reinstall everything on its first start. See docker-entrypoint.sh.
RUN md5sum package-lock.json > /app/node_modules/.lockstamp

# `sed` before `chmod`: this repository is developed on Windows and carries no
# .gitattributes older than this file, so a clone with core.autocrlf=true would
# otherwise hand /bin/sh a script whose every line ends in CR — which fails as
# "bad interpreter" or, worse, as a stamp path with an invisible character in
# it. Stripping it here makes the image immune to the checkout's git config.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh

# No `COPY . .`, deliberately. The compose service bind-mounts this directory
# over /app — it has to, because `npm run build` must write `dist/` where the
# FastAPI service can find it and `_mount_console` looks on the host. A second
# copy of the source baked into the image would be shadowed by that mount on
# every single run: a copy nobody reads and everybody eventually wonders about.
# The consequence, stated rather than discovered: this image does nothing useful
# without the mount, and `docker compose` is the supported way to run it.

EXPOSE 5173

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
