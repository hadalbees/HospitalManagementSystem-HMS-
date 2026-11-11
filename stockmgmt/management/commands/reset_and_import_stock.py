import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from stockmgmt.stock_importer import reset_and_import_stock

User = get_user_model()

class Command(BaseCommand):
    help = "Super Admin only command to delete old stock and import clean stock from August pharmacy stock.xlsx"

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='August pharmacy stock.xlsx',
            help='Path to the Excel/CSV file to import (default: August pharmacy stock.xlsx)'
        )
        parser.add_argument(
            '--no-clear',
            action='store_true',
            help='Do not clear existing stock records before importing'
        )

    def handle(self, *args, **options):
        file_path = options['file']
        clear_existing = not options['no_clear']

        if not os.path.exists(file_path):
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        super_user = User.objects.filter(is_superuser=True).first()
        if not super_user:
            self.stdout.write(self.style.WARNING("Warning: No superuser found in database. Running as system."))

        self.stdout.write(self.style.WARNING(f"Starting stock reset & import (clear_existing={clear_existing})..."))
        try:
            stats = reset_and_import_stock(
                file_path_or_df=file_path,
                user=super_user,
                clear_existing_stock=clear_existing
            )
            self.stdout.write(self.style.SUCCESS(
                f"Successfully completed!\n"
                f" - Deleted old stocks: {stats['deleted_stocks']}\n"
                f" - Created new stocks: {stats['created_stocks']}\n"
                f" - Medicines processed: {stats['medicines_handled']}\n"
                f" - Categories created: {stats['categories_created']}\n"
                f" - Manufacturers created: {stats['manufacturers_created']}\n"
                f" - Suppliers created: {stats['suppliers_created']}"
            ))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Import failed: {e}"))

