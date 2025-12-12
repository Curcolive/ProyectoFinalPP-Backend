from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cupones', '0004_cuota_saldo_pendiente_pagoparcial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuponpago',
            name='es_pago_parcial',
            field=models.BooleanField(default=False),
        ),
    ]