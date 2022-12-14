#!/bin/sh

# Check if config exists taking one from image if needed.
if [ ! "$(ls -A --ignore=.gitkeep ./app/config)" ]; then
    echo "\033[36mWrapper | \033[33mðï¸  Config not found, pulling defaults..."
    mkdir ./app/config/ 2> /dev/null
    cp -r ./app/config_original/* ./app/config/

    if [ $(id -u) -eq 0 ]; then
        # We're running as root;
        # Look for files & dirs that require group permissions to be fixed
        # This will do the full /crafty dir, so will take a miniute.
        echo "\033[36mWrapper | \033[35mð Looking for problem bind mount permissions globally..."
        find . ! -group root -exec chgrp root {} \;
        find . ! -perm g+rw -exec chmod g+rw {} \;
        find . -type d ! -perm g+s -exec chmod g+s {} \;
    fi
else
    # Keep version file up to date with image
    cp -f ./app/config_original/version.json ./app/config/version.json

    # Compare if user's config is different from image, and show differences.
    echo "\033[36mWrapper | \033[35mðï¸  Checking for config.json changes..."
    cp -f ./app/config_original/config.json ./app/config/config_image_template
    if [ "$(diff -q ./app/config/config.json ./app/config/config_image_template)" ]; then
        echo "\033[36mWrapper | \033[33mð· We've found differences in your local config, please review!,"
        echo "\033[36m        | \033[33m   (This could be an outdated config.json)"
    else
        echo "\033[36mWrapper | \033[32mâ Config good! Proceeding..."
    fi
fi


if [ $(id -u) -eq 0 ]; then
    # We're running as root

    # If we find files in import directory, we need to ensure all dirs are owned by the root group,
    # This fixes bind mounts that may have incorrect perms.
    if [ "$(ls -A --ignore=.gitkeep ./import)" ]; then
        echo "\033[36mWrapper | \033[35mð Files present in import directory, checking/fixing permissions..."
        echo "\033[36mWrapper | \033[33mâ³ Please be paitent for larger servers..."
        find . ! -group root -exec chgrp root {} \;
        find . ! -perm g+rw -exec chmod g+rw {} \;
        find . -type d ! -perm g+s -exec chmod g+s {} \;
        echo "\033[36mWrapper | \033[32mâ Permissions Fixed! (This will happen every boot until /import is empty!)"
    fi

    # Switch user, activate our prepared venv and lauch crafty
    args="$@"
    echo "\033[36mWrapper | \033[32mð Launching crafty with [\033[34m$args\033[32m]"
    exec sudo -u crafty bash -c "source ./.venv/bin/activate && exec nice --adjustment=-20 python3 main.py $args"
else
    # Activate our prepared venv
    echo "\033[36mWrapper | \033[32mð Non-root host detected, using normal exec"
    . ./.venv/bin/activate
    # Use exec as our perms are already correct
    # This is likely if using Kubernetes/OpenShift etc
    exec nice --adjustment=-20 python3 main.py $@
fi
