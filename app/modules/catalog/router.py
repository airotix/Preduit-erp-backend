"""Catalog HTTP routes. Returns ScreenConfig for the frontend's list renderer,
and a real create endpoint (replacing the frontend's stubbed mutation)."""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.deps import tenant_db
from app.core.security import Principal, require_module
from app.modules.catalog import service
from app.modules.catalog import retirement
from app.modules.catalog.dto import (
    AttributeCreate, AttributeUpdate, CategoryCreate, CategoryUpdate,
    ProductCreate, ProductUpdate, ProductEditorSave, VariantPricesUpdate, VariantGtinsUpdate,
    StockDisposition, StockReconciliation,
)
from app.modules.inventory.dto import MatrixUpdate

router = APIRouter(prefix="/catalog", tags=["catalog"])

read = require_module("catalog", "read")
write = require_module("catalog", "write")
inventory_write = require_module("inventory", "write")
shipment_write = require_module("shipments", "write")


@router.get("/products/{public_id}/tech-pack")
def product_tech_pack(public_id: str, principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    from app.modules import techpacks
    return techpacks.product_pack(db, techpacks.product_by_public(db, public_id, principal.tenant_id))


@router.post("/products/{public_id}/tech-pack", status_code=status.HTTP_201_CREATED)
def upload_tech_pack(public_id: str, file: UploadFile = File(...),
                     principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules import techpacks
    from app.modules.documents.router import _read_capped, settings
    return techpacks.upload_product(db, tenant_id=principal.tenant_id, public_id=public_id,
        filename=file.filename or "file", content_type=file.content_type,
        data=_read_capped(file, settings.upload_max_bytes))


@router.post("/products/{public_id}/tech-pack/revisions/{revision_id}/approve")
def approve_tech_pack(public_id: str, revision_id: UUID,
                      principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    from app.modules import techpacks
    return techpacks.approve_product_revision(db, tenant_id=principal.tenant_id,
        public_id=public_id, revision_id=revision_id, actor=principal.email)


@router.get("/products/screen")
def products_screen(
    limit: int = Query(25, le=200), offset: int = Query(0, ge=0),
    _: Principal = Depends(read), db: Session = Depends(tenant_db),
):
    """ScreenConfig for /catalog/products (frontend BFF endpoint)."""
    return service.products_screen(db, limit=limit, offset=offset)


@router.get("/products/search")
def products_search(q: str = Query("", max_length=100),
                    limit: int = Query(1000, le=2000),
                    _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Type-ahead product suggestions (name + suggested price) — also used by
    Sales/Procurement line editors, which is why every role able to write
    orders or POs already carries at least catalog.read."""
    return service.search_products(db, q=q, limit=limit)


@router.get("/colors")
def colors(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Color options for order/PO line editors."""
    return service.list_colors(db)


@router.get("/sizes")
def sizes(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Ordered size scale for PO size breakdowns."""
    return service.list_sizes(db)


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    principal: Principal = Depends(write),
    db: Session = Depends(tenant_db),
):
    product = service.create_product(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(product.public_id), "title": product.title, "status": product.status}


@router.put("/products/{public_id}")
def update_product(public_id: str, payload: ProductUpdate,
                   principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    product = service.update_product(db, tenant_id=principal.tenant_id,
                                     public_id=public_id, payload=payload)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(product.public_id), "title": product.title}


@router.put("/products/{public_id}/editor")
def save_product_editor(public_id: str, payload: ProductEditorSave,
                        principal: Principal = Depends(write),
                        db: Session = Depends(tenant_db)):
    product = service.save_product_editor(db, tenant_id=principal.tenant_id,
                                          public_id=public_id, payload=payload)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(product.public_id), "title": product.title}


@router.put("/products/{public_id}/variant-prices")
def update_variant_prices(public_id: str, payload: VariantPricesUpdate,
                          principal: Principal = Depends(write),
                          db: Session = Depends(tenant_db)):
    product = service.update_variant_prices(db, tenant_id=principal.tenant_id,
                                            public_id=public_id, payload=payload)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(product.public_id)}


@router.put("/products/{public_id}/variant-gtins")
def update_variant_gtins(public_id: str, payload: VariantGtinsUpdate,
                         principal: Principal = Depends(write),
                         db: Session = Depends(tenant_db)):
    product = service.update_variant_gtins(db, tenant_id=principal.tenant_id,
                                           public_id=public_id, payload=payload)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(product.public_id)}


@router.get("/products/{public_id}/delete-preview")
def product_delete_preview(public_id: str, principal: Principal = Depends(read),
                           db: Session = Depends(tenant_db)):
    return retirement.preview(db, tenant_id=principal.tenant_id, public_id=public_id)


@router.delete("/products/{public_id}")
def delete_product(public_id: str, principal: Principal = Depends(write),
                   db: Session = Depends(tenant_db)):
    return retirement.retire(db, tenant_id=principal.tenant_id, public_id=public_id)


@router.post("/products/{public_id}/stock-disposition")
def product_stock_disposition(public_id: str, payload: StockDisposition,
                              principal: Principal = Depends(write),
                              _: Principal = Depends(inventory_write),
                              db: Session = Depends(tenant_db)):
    return retirement.dispose_stock(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload, actor=principal.email)


@router.post("/products/{public_id}/reconcile-stock-cache")
def product_reconcile_stock_cache(public_id: str, payload: StockReconciliation,
                                  principal: Principal = Depends(write),
                                  _: Principal = Depends(inventory_write),
                                  db: Session = Depends(tenant_db)):
    return retirement.reconcile_stock_cache(db, tenant_id=principal.tenant_id,
                                            public_id=public_id, reason=payload.reason)


@router.post("/products/{public_id}/shipments/{shipment_id}/cancel")
def cancel_article_shipment(public_id: str, shipment_id: UUID, payload: StockReconciliation,
                            principal: Principal = Depends(write),
                            _: Principal = Depends(shipment_write), db: Session = Depends(tenant_db)):
    return retirement.cancel_article_shipment(db, tenant_id=principal.tenant_id,
        public_id=public_id, shipment_id=shipment_id, reason=payload.reason)


@router.put("/products/{public_id}/image")
def set_product_image(public_id: str, payload: dict,
                      principal: Principal = Depends(write),
                      db: Session = Depends(tenant_db)):
    p = service.set_product_image(db, public_id=public_id, image_url=payload.get("imageUrl"))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(p.public_id)}


@router.get("/products/{public_id}/detail")
def product_detail(public_id: str, _: Principal = Depends(read), db: Session = Depends(tenant_db)):
    detail = service.product_detail(db, public_id=public_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return detail


@router.put("/products/{public_id}/matrix")
def save_product_matrix(public_id: str, payload: MatrixUpdate,
                        principal: Principal = Depends(write),
                        db: Session = Depends(tenant_db)):
    p = service.save_product_matrix(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(p.public_id)}


@router.get("/categories")
def category_names(_: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Flat list of category names for the product form's Category picker —
    kept in sync automatically, unlike a hardcoded option list."""
    return service.list_category_names(db)


@router.get("/seasons")
def season_names(principal: Principal = Depends(read), db: Session = Depends(tenant_db)):
    """Previously saved season names for the product form's suggestions."""
    return service.list_seasons(db, tenant_id=principal.tenant_id)


@router.get("/categories/screen")
def categories_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    _: Principal = Depends(read), db: Session = Depends(tenant_db),
):
    return service.categories_screen(db, limit=limit, offset=offset)


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    principal: Principal = Depends(write),
    db: Session = Depends(tenant_db),
):
    category = service.create_category(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(category.public_id), "name": category.name}


@router.put("/categories/{public_id}")
def update_category(public_id: str, payload: CategoryUpdate,
                    principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    c = service.update_category(db, public_id=public_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return {"public_id": str(c.public_id), "name": c.name}


@router.delete("/categories/{public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(public_id: UUID, principal: Principal = Depends(write),
                    db: Session = Depends(tenant_db)):
    service.delete_category(db, tenant_id=principal.tenant_id, public_id=public_id)


@router.get("/attributes/screen")
def attributes_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    _: Principal = Depends(read), db: Session = Depends(tenant_db),
):
    return service.attributes_screen(db, limit=limit, offset=offset)


@router.post("/attributes", status_code=status.HTTP_201_CREATED)
def create_attribute(
    payload: AttributeCreate,
    principal: Principal = Depends(write),
    db: Session = Depends(tenant_db),
):
    attr = service.create_attribute(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(attr.public_id), "value": attr.value, "type": attr.attr_type}


@router.put("/attributes/{public_id}")
def update_attribute(public_id: str, payload: AttributeUpdate,
                     principal: Principal = Depends(write), db: Session = Depends(tenant_db)):
    attr = service.update_attribute(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload)
    if attr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attribute not found")
    return {"public_id": str(attr.public_id), "value": attr.value, "type": attr.attr_type}
