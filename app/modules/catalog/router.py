"""Catalog HTTP routes. Returns ScreenConfig for the frontend's list renderer,
and a real create endpoint (replacing the frontend's stubbed mutation)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.catalog import service
from app.modules.catalog.dto import (
    AttributeCreate, AttributeUpdate, CategoryCreate, CategoryUpdate,
    ProductCreate, ProductUpdate,
)
from app.modules.inventory.dto import MatrixUpdate

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products/screen")
def products_screen(
    limit: int = Query(25, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    """ScreenConfig for /catalog/products (frontend BFF endpoint)."""
    return service.products_screen(db, limit=limit, offset=offset)


@router.get("/products/search")
def products_search(q: str = Query("", max_length=100),
                    limit: int = Query(10, le=25),
                    db: Session = Depends(tenant_db)):
    """Type-ahead product suggestions (name + suggested price)."""
    return service.search_products(db, q=q, limit=limit)


@router.get("/colors")
def colors(db: Session = Depends(tenant_db)):
    """Color options for order/PO line editors."""
    return service.list_colors(db)


@router.get("/sizes")
def sizes(db: Session = Depends(tenant_db)):
    """Ordered size scale for PO size breakdowns."""
    return service.list_sizes(db)


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    product = service.create_product(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(product.public_id), "title": product.title, "status": product.status}


@router.put("/products/{public_id}")
def update_product(public_id: str, payload: ProductUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    product = service.update_product(db, tenant_id=principal.tenant_id,
                                     public_id=public_id, payload=payload)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(product.public_id), "title": product.title}


@router.put("/products/{public_id}/image")
def set_product_image(public_id: str, payload: dict,
                      principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    p = service.set_product_image(db, public_id=public_id, image_url=payload.get("imageUrl"))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(p.public_id)}


@router.get("/products/{public_id}/detail")
def product_detail(public_id: str, db: Session = Depends(tenant_db)):
    detail = service.product_detail(db, public_id=public_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return detail


@router.put("/products/{public_id}/matrix")
def save_product_matrix(public_id: str, payload: MatrixUpdate,
                        principal: Principal = Depends(require_tenant),
                        db: Session = Depends(tenant_db)):
    p = service.save_product_matrix(db, tenant_id=principal.tenant_id,
                                    public_id=public_id, payload=payload)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return {"public_id": str(p.public_id)}


@router.get("/categories/screen")
def categories_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.categories_screen(db, limit=limit, offset=offset)


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    category = service.create_category(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(category.public_id), "name": category.name}


@router.put("/categories/{public_id}")
def update_category(public_id: str, payload: CategoryUpdate,
                    principal: Principal = Depends(require_tenant),
                    db: Session = Depends(tenant_db)):
    c = service.update_category(db, public_id=public_id, payload=payload)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return {"public_id": str(c.public_id), "name": c.name}


@router.get("/attributes/screen")
def attributes_screen(
    limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(tenant_db),
):
    return service.attributes_screen(db, limit=limit, offset=offset)


@router.post("/attributes", status_code=status.HTTP_201_CREATED)
def create_attribute(
    payload: AttributeCreate,
    principal: Principal = Depends(require_tenant),
    db: Session = Depends(tenant_db),
):
    attr = service.create_attribute(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(attr.public_id), "value": attr.value, "type": attr.attr_type}
