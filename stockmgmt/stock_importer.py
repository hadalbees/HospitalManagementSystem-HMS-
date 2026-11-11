import re
import pandas as pd
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from fieldmgmt.models import AyurvedicType, ManufacturerMaster, SupplierMaster
from stockmgmt.models import Medicine, Stock, StockTransaction


def parse_date(val):
    if pd.isna(val) or not str(val).strip():
        return None
    val_str = str(val).strip().lower()

    # Pattern like '10.2024' -> 2024-10-01
    m_my = re.match(r'^(\d{1,2})\.(\d{4})$', val_str)
    if m_my:
        month, year = int(m_my.group(1)), int(m_my.group(2))
        return f"{year}-{month:02d}-01"

    # Pattern like 'agu-25', 'agu-27', 'agu-34'
    m_mon = re.match(r'^([a-z]{3})[-.]?(\d{2})$', val_str)
    if m_mon:
        mon_str, yr_short = m_mon.group(1), int(m_mon.group(2))
        year = 2000 + yr_short
        months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                  'jul': 7, 'agu': 8, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
        month = months.get(mon_str, 8)
        return f"{year}-{month:02d}-01"

    # Standard pandas to_datetime
    try:
        dt = pd.to_datetime(val_str, errors='coerce')
        if pd.notna(dt):
            year = dt.year
            # Fix Excel 2-digit century bug: e.g. 1933 -> 2033
            if 1920 <= year <= 1950:
                year += 100
            return f"{year}-{dt.month:02d}-{dt.day:02d}"
    except Exception:
        pass
    return None


def parse_quantity(val, pack_size):
    if pd.isna(val) or not str(val).strip():
        return Decimal("0.00"), Decimal("0.00")

    val_str = str(val).strip().upper()

    # 9L -> 9000 ml
    if val_str == '9L':
        total_base = Decimal("9000.00")
        packs = total_base / pack_size if pack_size > 0 else Decimal("0.00")
        return packs, total_base

    # '4-100ML' -> 4 packs + 100ml
    m_plus = re.match(r'^(\d+)[-+](\d+)\s*(ML|G)?$', val_str)
    if m_plus:
        full_packs = Decimal(m_plus.group(1))
        extra_base = Decimal(m_plus.group(2))
        total_base = (full_packs * pack_size) + extra_base
        packs = total_base / pack_size if pack_size > 0 else Decimal("0.00")
        return packs, total_base

    # '2.100ML' -> 2 packs + 100ml
    m_dot_ml = re.match(r'^(\d+)\.(\d+)\s*(ML|G)?$', val_str)
    if m_dot_ml:
        full_packs = Decimal(m_dot_ml.group(1))
        extra_str = m_dot_ml.group(2)
        extra_base = Decimal(extra_str)
        total_base = (full_packs * pack_size) + extra_base
        packs = total_base / pack_size if pack_size > 0 else Decimal("0.00")
        return packs, total_base

    # Plain numeric or float
    clean_val = re.sub(r'[^\d.]', '', val_str)
    if clean_val:
        num = Decimal(clean_val)
        if pack_size > 1 and num >= pack_size:
            total_base = num
            packs = total_base / pack_size
        else:
            packs = num
            total_base = packs * pack_size
        return packs, total_base

    return Decimal("0.00"), Decimal("0.00")


def determine_form_and_unit(category, medicine_name):
    cat_upper = (category or "").upper()
    med_lower = (medicine_name or "").lower()

    if any(k in cat_upper for k in ['ARISTAM', 'ASAVAM', 'TAILAM', 'KASHAYAM', 'SYRUP', 'DROPS', 'OIL', 'LIQUID', 'DANYAMLAM']):
        return 'LIQUID', 'ml'
    elif any(k in cat_upper for k in ['CHURAM', 'CHURNAM', 'BHASMA', 'POWDER']):
        return 'POWDER', 'g'
    elif any(k in cat_upper for k in ['TABLET', 'CAPSULE', 'GUTIKA', 'VATIKA', 'VATI', 'SOAP']):
        return 'PIECE', 'piece'
    else:
        if 'ml' in med_lower:
            return 'LIQUID', 'ml'
        elif 'gm' in med_lower or ' g' in med_lower or 'powder' in med_lower:
            return 'POWDER', 'g'
        else:
            return 'PIECE', 'piece'


def reset_and_import_stock(file_path_or_df, user=None, clear_existing_stock=True):
    """
    Super Admin only stock reset and clean import function.
    Handles ForeignKeys via get_or_create, cleans dirty data, and updates stock safely.
    """
    if user and not user.is_superuser:
        raise PermissionDenied("Only Super Admin can reset and import stock.")

    if isinstance(file_path_or_df, pd.DataFrame):
        df = file_path_or_df
    elif hasattr(file_path_or_df, 'read'):
        # Uploaded file object
        try:
            df = pd.read_excel(file_path_or_df)
        except Exception:
            file_path_or_df.seek(0)
            df = pd.read_csv(file_path_or_df)
    elif isinstance(file_path_or_df, str):
        if file_path_or_df.endswith('.xlsx') or file_path_or_df.endswith('.xls'):
            df = pd.read_excel(file_path_or_df)
        else:
            df = pd.read_csv(file_path_or_df)
    else:
        raise ValueError("Invalid file format or data source.")

    # Standardize column names
    col_map = {}
    for col in df.columns:
        c_clean = str(col).strip().upper()
        col_map[col] = c_clean
    df = df.rename(columns=col_map)

    stats = {
        'deleted_stocks': 0,
        'created_stocks': 0,
        'medicines_handled': 0,
        'categories_created': 0,
        'manufacturers_created': 0,
        'suppliers_created': 0,
    }

    with transaction.atomic():
        if clear_existing_stock:
            # Delete existing stock transactions and stocks safely
            tx_count = StockTransaction.objects.count()
            st_count = Stock.objects.count()
            StockTransaction.objects.all().delete()
            Stock.objects.all().delete()
            stats['deleted_stocks'] = st_count

        current_category_name = "General"
        current_category_obj, _ = AyurvedicType.objects.get_or_create(name=current_category_name)

        # Identify columns
        name_col = next((c for c in df.columns if 'MEDICINE' in c or c == 'NAME'), 'MEDICINE NAME')
        mfg_col = next((c for c in df.columns if 'MANUFACTURER' in c), 'MANUFACTURER')
        supp_col = next((c for c in df.columns if 'SUPPLIER' in c), 'SUPPLIER')
        pkg_col = next((c for c in df.columns if 'PACKAGE' in c), 'PACKAGE')
        qty_col = next((c for c in df.columns if any(k in c for k in ['STOKE', 'STOCK', 'NO OF PACK', 'QTY'])), 'STOKE ')
        sell_col = next((c for c in df.columns if any(k in c for k in ['SELL', 'SELLING'])), 'SELL PRICE')
        pur_col = next((c for c in df.columns if any(k in c for k in ['PURCHASE', 'BUY', 'UNIT PRICE'])), 'PURCHASE PRICE')
        batch_col = next((c for c in df.columns if 'BATCH' in c), 'BATCH NO')
        mfg_date_col = next((c for c in df.columns if 'MANUFACTURE DATE' in c or 'MFG' in c), 'MANUFACTURE DATE ')
        exp_date_col = next((c for c in df.columns if 'EXPIRY' in c or 'EXP' in c), 'EXPIRY')
        hsn_col = next((c for c in df.columns if 'HSN' in c), 'HSN code')

        for _, row in df.iterrows():
            med_name = str(row.get(name_col, '')).strip()
            if not med_name or med_name.lower() == 'nan' or med_name.startswith('---'):
                continue

            # Category row check
            mfg_raw = str(row.get(mfg_col, '')).strip()
            batch_raw = str(row.get(batch_col, '')).strip()
            price_raw = str(row.get(sell_col, '')).strip()

            if (not mfg_raw or mfg_raw.lower() == 'nan') and \
               (not batch_raw or batch_raw.lower() == 'nan') and \
               (not price_raw or price_raw.lower() == 'nan'):
                current_category_name = med_name
                current_category_obj, created = AyurvedicType.objects.get_or_create(name=current_category_name)
                if created:
                    stats['categories_created'] += 1
                continue

            # Form type & Base unit
            form_type, base_unit = determine_form_and_unit(current_category_name, med_name)

            # Clean pack size
            pack_val = str(row.get(pkg_col, '')).strip()
            clean_pack = re.sub(r'[^\d.]', '', pack_val)
            pack_size = Decimal(clean_pack) if clean_pack else Decimal("1.00")
            if pack_size <= 0:
                pack_size = Decimal("1.00")

            # Parse Quantity
            stock_val = row.get(qty_col)
            number_of_packs, total_base_qty = parse_quantity(stock_val, pack_size)

            # Prices
            sell_clean = re.sub(r'[^\d.]', '', str(row.get(sell_col, '')))
            selling_price = Decimal(sell_clean) if sell_clean else Decimal("0.00")

            pur_clean = re.sub(r'[^\d.]', '', str(row.get(pur_col, '')))
            unit_price = Decimal(pur_clean) if pur_clean else selling_price

            price_per_base = (selling_price / pack_size) if pack_size > 0 else Decimal("0.00")

            # Dates
            mfg_date = parse_date(row.get(mfg_date_col))
            exp_date = parse_date(row.get(exp_date_col))

            # Batch
            batch_no = batch_raw if batch_raw and batch_raw.lower() != 'nan' else 'DEFAULT'

            # HSN
            hsn = re.sub(r'[^\d]', '', str(row.get(hsn_col, ''))) if pd.notna(row.get(hsn_col)) else ''

            # Manufacturer
            mfg_name = mfg_raw if mfg_raw and mfg_raw.lower() != 'nan' else 'General'
            mfg_obj, created = ManufacturerMaster.objects.get_or_create(name=mfg_name)
            if created:
                stats['manufacturers_created'] += 1

            # Supplier
            supp_raw = str(row.get(supp_col, '')).strip()
            supp_name = supp_raw if supp_raw and supp_raw.lower() != 'nan' else 'General'
            supp_obj, created = SupplierMaster.objects.get_or_create(name=supp_name)
            if created:
                stats['suppliers_created'] += 1

            # Medicine (get or create)
            med_obj, med_created = Medicine.objects.get_or_create(
                name=med_name,
                defaults={
                    'form_type': form_type,
                    'ayurvedic_type': current_category_obj,
                    'hsn_code': hsn,
                    'gst_percentage': Decimal("0.00"),
                    'reorder_level': 10,
                    'expiry_days_alert': 30
                }
            )
            # Update category/hsn if already existed
            if not med_created:
                if current_category_obj and not med_obj.ayurvedic_type:
                    med_obj.ayurvedic_type = current_category_obj
                if hsn and not med_obj.hsn_code:
                    med_obj.hsn_code = hsn
                med_obj.save()

            stats['medicines_handled'] += 1

            # Create Stock record
            stock = Stock.objects.create(
                medicine=med_obj,
                batch_number=batch_no,
                base_unit=base_unit,
                unit_price=unit_price,
                selling_price=selling_price,
                gst_amount=Decimal("0.00"),
                pack_size=round(pack_size, 2),
                number_of_packs=round(number_of_packs, 2),
                total_base_quantity=round(total_base_qty, 2),
                price_per_base_unit=round(price_per_base, 6),
                manufacture_date=mfg_date,
                expiry_date=exp_date,
                supplier=supp_obj,
                manufacturer=mfg_obj,
                last_updated_by=user,
                created_at=timezone.now(),
                updated_at=timezone.now()
            )
            stats['created_stocks'] += 1

            # Create audit trail transaction
            if total_base_qty > 0:
                StockTransaction.objects.create(
                    stock_item=stock,
                    transaction_type='PURCHASE',
                    quantity_change=round(total_base_qty, 2),
                    transaction_date=timezone.now(),
                    recorded_by=user,
                    notes="Super Admin initial inventory import from August pharmacy stock"
                )

    return stats

