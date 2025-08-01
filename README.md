# GitHub Actions Library – Innogando

A collection of reusable **GitHub composite actions** designed to automate Innogando's CI/CD processes. This library centralizes the most commonly used automation tools across the organization, standardizing workflows and simplifying configuration across multiple projects.

## 🎯 Purpose

- **Reusability**: Eliminates code duplication in workflows across multiple repositories
- **Standardization**: Ensures consistent CI/CD practices throughout the organization
- **Maintenance**: Centralizes automation logic for easier updates
- **Efficiency**: Reduces setup time for new projects

## 📦 Available Actions

### 🔧 Linting and Code Quality

#### `flutter-linter`
Runs static analysis and format verification on Flutter projects.
- **Tools**: `dart format`, `flutter analyze`
- **Features**: Excludes encrypted files from formatting
- **Configuration**: Customizable Flutter version

#### `python-linter`
Applies linting to Python code using best practices.
- **Tools**: `isort`, `black`
- **Target directory**: `./src`

### 🚀 Build and Deployment

#### `android-app-build`
Builds Flutter APKs for Android and distributes them automatically.
- **Features**:
  - Secret unlocking with git-crypt
  - ARM64-optimized builds
  - Automatic Telegram delivery with changelog
- **Required inputs**: `project_name`, `git_crypt_key`, `telegram_token`, `telegram_chat_id`

#### `web-app-deploy`
Deploys Flutter web applications to remote servers.
- **Features**:
  - Optimized web builds
  - Secure connection via WireGuard
  - Automatic deployment with nginx restart
- **Infrastructure**: Compatible with AWS servers

#### `docker-deploy`
Automates containerized service deployment.
- **Complete process**:
  - Docker image building
  - Secure transfer to remote server
  - Deployment with docker-compose
  - Automatic service restart

### 🔄 Code Management

#### `sync-branches`
Automatically synchronizes Git branches.
- **Typical use**: Keep development branches up to date
- **Strategy**: Merge with automatic conflict resolution

#### `check-build-number-flutter`
Validates correct build number increments in Flutter.
- **Verification**: Compares against main branch
- **Automation**: Prevents manual versioning errors

## 📋 Usage

To use any of these actions in your workflows, reference the repository using the `uses` syntax:

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - name: Lint Flutter Code
        uses: Innogando/github-workflows/flutter-linter@main
        
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Production
        uses: Innogando/github-workflows/docker-deploy@main
        with:
          project_name: my-app
          ssh_host: ${{ secrets.SSH_HOST }}
          # ... other required parameters
```

## 🔒 Security

The actions implement the following security measures:
- **git-crypt**: For secure secret management
- **WireGuard**: For secure VPN connections to servers
- **SSH**: For file transfers and remote execution
- **Environment variables**: For secure credential handling

## 🤝 Contributing

To add new actions or improve existing ones:

1. **Fork** the repository
2. **Create** a new branch for your feature
3. **Develop** your action following the existing structure:
   ```
   new-action/
   └── action.yml
   ```
4. **Test** thoroughly in a development environment
5. **Submit** a pull request with detailed description

### Development Standards

- Use **composite actions** for maximum reusability
- **Document** all inputs and outputs
- **Include** clear descriptions and usage examples
- **Follow** existing naming conventions

## 📄 License

MIT License - See [LICENSE](LICENSE) file for more details.

## 📞 Support

To report issues or request new features, use GitHub's issue system or contact Innogando's DevOps team.
