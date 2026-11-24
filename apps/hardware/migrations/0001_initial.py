import apps.hardware.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('customers', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Supplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('supplier_code', models.CharField(default=apps.hardware.models.generate_supplier_code, max_length=20, unique=True, verbose_name='供應商代號')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='供應商名稱')),
                ('contact', models.CharField(blank=True, max_length=100, verbose_name='聯絡人')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='電子郵件')),
                ('phone', models.CharField(blank=True, max_length=50, verbose_name='電話')),
                ('address', models.CharField(blank=True, max_length=200, verbose_name='地址')),
                ('note', models.CharField(blank=True, max_length=200, null=True, verbose_name='備註')),
            ],
            options={
                'verbose_name': '供應商',
                'verbose_name_plural': '供應商',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='HardwareModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('model_code', models.CharField(default=apps.hardware.models.generate_model_code, max_length=20, unique=True, verbose_name='型號代碼')),
                ('name', models.CharField(max_length=50, unique=True, verbose_name='型號名稱')),
                ('port_count', models.PositiveIntegerField(verbose_name='網路連接埠數')),
                ('cpu', models.CharField(blank=True, max_length=50, verbose_name='CPU 規格')),
                ('memory', models.CharField(blank=True, max_length=50, verbose_name='記憶體')),
                ('hdd', models.CharField(blank=True, max_length=50, verbose_name='硬碟空間')),
                ('note', models.CharField(blank=True, max_length=200, null=True, verbose_name='規格備註')),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='models', to='hardware.supplier', verbose_name='原廠/供應商')),
            ],
            options={
                'verbose_name': '硬體規格主檔',
                'verbose_name_plural': '硬體規格主檔',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='HardwareDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=50, unique=True, verbose_name='設備序號/資產編號')),
                ('status', models.CharField(choices=[('available', '庫存中'), ('shipped', '已出貨'), ('loaned', '借測中'), ('scrapped', '已報廢')], default='available', max_length=20, verbose_name='設備狀態')),
                ('location', models.CharField(blank=True, max_length=100, verbose_name='存放位置')),
                ('acquired_at', models.DateField(blank=True, null=True, verbose_name='購入/進貨日')),
                ('expected_return_at', models.DateField(blank=True, null=True, verbose_name='預計歸還日')),
                ('note', models.CharField(blank=True, max_length=200, null=True, verbose_name='備註')),
                ('port_count', models.PositiveIntegerField(blank=True, null=True, verbose_name='連接埠數覆寫')),
                ('cpu', models.CharField(blank=True, max_length=50, null=True, verbose_name='CPU 覆寫')),
                ('memory', models.CharField(blank=True, max_length=50, null=True, verbose_name='記憶體覆寫')),
                ('hdd', models.CharField(blank=True, max_length=50, null=True, verbose_name='硬碟覆寫')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='建立時間')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='最後更新')),
                ('is_deleted', models.BooleanField(default=False, verbose_name='已刪除')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hardware_devices', to='customers.customer', verbose_name='關聯客戶')),
                ('model', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='devices', to='hardware.hardwaremodel', verbose_name='設備型號')),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devices', to='hardware.supplier', verbose_name='進貨供應商')),
            ],
            options={
                'verbose_name': '硬體設備庫存',
                'verbose_name_plural': '硬體設備庫存',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='InventoryTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('IN', '進貨入庫'), ('OUT', '正式出貨'), ('LOAN', '客戶借測'), ('RETURN_LOAN', '借測歸還'), ('RETURN_OUT', '出貨退貨'), ('SCRAP', '設備報廢')], max_length=20, verbose_name='交易類別')),
                ('quantity', models.PositiveIntegerField(default=1, verbose_name='數量')),
                ('occurred_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='發生時間')),
                ('expected_return_at', models.DateField(blank=True, null=True, verbose_name='預計歸還日')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='交易備註')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_transactions', to=settings.AUTH_USER_MODEL, verbose_name='經手人員')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_transactions', to='customers.customer', verbose_name='交易客戶')),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='hardware.hardwaredevice', verbose_name='關聯設備')),
            ],
            options={
                'verbose_name': '庫存交易紀錄',
                'verbose_name_plural': '庫存交易紀錄',
                'ordering': ['-occurred_at', '-id'],
            },
        ),
    ]
