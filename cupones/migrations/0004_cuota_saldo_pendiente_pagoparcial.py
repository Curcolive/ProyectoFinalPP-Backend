from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cupones', '0003_perfil'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuota',
            name='saldo_pendiente',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.CreateModel(
            name='PagoParcial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monto', models.DecimalField(decimal_places=2, max_digits=10)),
                ('fecha', models.DateTimeField(auto_now_add=True)),
                ('medio_pago', models.CharField(default='Macro Click', max_length=100)),
                ('cuota', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pagos_parciales', to='cupones.cuota')),
            ],
        ),
    ]