name: Price Mailer Cron
on:
  schedule:
    # Запускать каждый день в 2:55 UTC (это 5:55 МСК)
       - cron: '55 2 * * *' # 05:55 МСК  
  workflow_dispatch:
    # Позволяет запускать воркфлоу вручную из интерфейса GitHub Actions
jobs:
  send_price_list:
    runs-on: ubuntu-latest
    env:
      FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'
    steps:
      - name: 1. Checkout repository
        uses: actions/checkout@v4
      - name: 2. Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - name: 3. Create config.json from Secrets
        working-directory: ./price_mailer/run
        run: |
          echo '{
              "imap": {
                  "server": "imap.mail.ru",
                  "port": 993,
                  "login": "${{ secrets.IMAP_LOGIN }}",
                  "password": "${{ secrets.IMAP_PASSWORD }}"
              },
              "smtp": {
                  "server": "smtp.yandex.ru",
                  "port": 465,
                  "login": "${{ secrets.SMTP_LOGIN }}",
                  "password": "${{ secrets.SMTP_PASSWORD }}"
              },
              "search_subject": "Прайс-лист",
              "recipients": ${{ secrets.RECIPIENTS }},
              "email_subject": "Актуальный прайс-лист",
              "email_body": "Добрый день!\n\nВо вложении актуальный прайс-лист.\n\nС уважением."
          }' > config.json
      - name: 4. Run the mailer script
        run: python price_mailer/run/price_mailer.py
        env:
          FROM_ADDRESS: ${{ secrets.FROM_ADDRESS }}
