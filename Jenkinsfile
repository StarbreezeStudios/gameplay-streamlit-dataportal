// Root Jenkinsfile — discovered by the Dataportal multibranch scanner.
// For now this deploys the single project (tutorial-path-explorer); when a
// second project is added, convert this into a dispatcher that builds only
// the changed project (e.g. with `changeset` filters per `projects/<name>/**`).
//
// Build context = monorepo root so the `shared/` package is included.
// UI: http://helsinki:8510

pipeline {
    agent { label ('helsinki') }

    options {
        timestamps()
        buildDiscarder(logRotator(daysToKeepStr: '365'))
        disableConcurrentBuilds()
    }

    environment {
        STACK_DIR    = '/opt/tutorial-path-explorer'
        PROJECT_DIR  = 'projects/tutorial-path-explorer'
    }

    stages {
        stage('Setup Directory') {
            steps {
                sh '''#!/bin/bash
                    set -e
                    if [ ! -d "${STACK_DIR}" ]; then
                        sudo mkdir -p "${STACK_DIR}"
                        sudo chown $(whoami):$(whoami) "${STACK_DIR}"
                    fi
                    echo "Stack directory ready: ${STACK_DIR}"
                '''
            }
        }

        stage('Deploy Configuration') {
            steps {
                sh '''#!/bin/bash
                    set -e
                    echo "Copying files from workspace..."

                    # shared package (monorepo root)
                    rm -rf "${STACK_DIR}/shared"
                    cp -r "${WORKSPACE}/shared" "${STACK_DIR}/shared"

                    # project subdirectory (docker-compose.yaml is inside this copy)
                    rm -rf "${STACK_DIR}/${PROJECT_DIR}"
                    mkdir -p "${STACK_DIR}/${PROJECT_DIR}"
                    cp -r "${WORKSPACE}/${PROJECT_DIR}/." "${STACK_DIR}/${PROJECT_DIR}/"

                    # Stack layout mirrors the monorepo so compose's `context: ../..`
                    # resolves to the stack root, where shared/ + projects/ live:
                    #   ${STACK_DIR}/
                    #     shared/
                    #     projects/<name>/
                    #       docker-compose.yaml
                    #       Dockerfile
                    #       .env, keys/  (written in the next stage)
                    echo "Deployed to ${STACK_DIR}:"
                    ls -la "${STACK_DIR}"
                    ls -la "${STACK_DIR}/${PROJECT_DIR}"
                '''
            }
        }

        stage('Setup Snowflake Credentials') {
            steps {
                withCredentials([sshUserPrivateKey(
                    credentialsId: 'snowflake_prod_credentials',
                    usernameVariable: 'SF_USER',
                    keyFileVariable: 'SF_KEY_PATH'
                )]) {
                    sh '''#!/bin/bash
                    set -e

                    ENV_FILE="${STACK_DIR}/${PROJECT_DIR}/.env"
                    mkdir -p "${STACK_DIR}/${PROJECT_DIR}/keys"

                    cp "$SF_KEY_PATH" "${STACK_DIR}/${PROJECT_DIR}/keys/snowflake_key.p8"
                    chmod 600 "${STACK_DIR}/${PROJECT_DIR}/keys/snowflake_key.p8"

                    cat > "$ENV_FILE" <<EOL
SNOWFLAKE_USER=$SF_USER
SNOWFLAKE_ACCOUNT=RE15009-STARBREEZE
SNOWFLAKE_WAREHOUSE=JENKINS_PROD
SNOWFLAKE_DATABASE=PAYDAY3_PROD
# No SNOWFLAKE_ROLE: the Jenkins user (JENKINS_PROD) does not have SYSADMIN.
# Letting Snowflake pick the user's default role is the right thing for
# Streamlit read-only consumption of dbt-managed tables.
SNOWFLAKE_KEY_DIR=./keys
EOL
                    chmod 600 "$ENV_FILE"
                    echo ".env written (user: $SF_USER)"
                '''
                }
            }
        }

        stage('Deploy Stack') {
            steps {
                sh '''#!/bin/bash
                    set -e
                    # docker-compose.yaml lives in the project subdir so its
                    # `context: ../..` resolves correctly to the stack root.
                    cd "${STACK_DIR}/${PROJECT_DIR}"

                    echo "=== Ports currently bound on Helsinki (8500-8599 range) ==="
                    ss -tln 2>/dev/null | awk 'NR==1 || /:85[0-9][0-9]/' || true
                    echo "=== Docker containers publishing 85xx ports ==="
                    docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' | grep -E '85[0-9]{2}' || echo "  (none from docker)"
                    echo "==="

                    echo "Stopping any existing container with the same name (in case compose lost track)..."
                    docker rm -f tutorial-path-explorer 2>/dev/null || true

                    echo "Starting Tutorial Path Explorer..."
                    docker compose down --remove-orphans || true
                    docker compose build --no-cache
                    docker compose up -d

                    echo "Waiting for service to start..."
                    sleep 15

                    curl -s --retry 10 --retry-delay 5 http://localhost:8510/_stcore/health > /dev/null \
                        && echo "Streamlit is up." \
                        || echo "Streamlit not responding yet (may still be initializing)."
                '''
            }
        }

        stage('Verify Deployment') {
            steps {
                sh '''#!/bin/bash
                    echo "=== Container Status ==="
                    docker compose -f "${STACK_DIR}/${PROJECT_DIR}/docker-compose.yaml" ps

                    echo ""
                    echo "Tutorial Path Explorer:  http://helsinki:8510"
                '''
            }
        }
    }

    post {
        success { echo 'Tutorial Path Explorer deployment completed successfully.' }
        failure {
            echo 'Tutorial Path Explorer deployment failed.'
            sh '''
                echo "=== Container Logs ==="
                cd "${STACK_DIR}/${PROJECT_DIR}" 2>/dev/null && docker compose logs --tail=50 || true
            '''
        }
    }
}
