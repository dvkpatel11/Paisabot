from datetime import datetime

from app.extensions import db


class SystemConfig(db.Model):
    __tablename__ = 'system_config'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    key = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Text, nullable=False)
    value_type = db.Column(db.String(10), default='string')
    description = db.Column(db.Text)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by = db.Column(db.String(50), default='system')

    __table_args__ = (
        db.UniqueConstraint('category', 'key', name='uq_config_cat_key'),
    )

    def __repr__(self):
        return f'<Config {self.category}.{self.key}={self.value}>'
