import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.config import SECRET_KEY, ALGORITHM

from app.models.users import User as UserModel
from app.models.products import Product as ProductModel
from app.models.reviews import Review as ReviewModel

from app.schemas import ReviewCreate, Review as ReviewSchema, RefreshTokenRequest

from app.db_depends import get_async_db

from app.auth import hash_password, verify_password, create_access_token, create_refresh_token, get_current_buyer, \
    get_current_user

router = APIRouter(prefix="/reviews", tags=["reviews"])

@router.get("/", response_model=list[ReviewSchema], status_code=status.HTTP_200_OK)
async def get_reviews(db: AsyncSession = Depends(get_async_db)):
    """
    Возвращает список всех отзывов.
    """
    stmt = select(ReviewModel).where(ReviewModel.is_active == True)
    reviews = await db.scalars(stmt)
    return reviews.all()

@router.get("/{product_id}/reviews", response_model=list[ReviewSchema], status_code=status.HTTP_200_OK)
async def get_reviews(product_id: int, db: AsyncSession = Depends(get_async_db)):
    """
    Получает отзывы о конкретном товаре.
    """
    # Проверяем существует ли товар
    product = await db.scalars(
        select(ProductModel).where(ProductModel.id == product_id, ProductModel.is_active == True)
    )
    product = product.first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    # Получаем список отзывов о товаре
    reviews = await db.scalars(
        select(ReviewModel).where(ReviewModel.product_id == product_id, ReviewModel.is_active == True)
    )
    return reviews.all()


@router.post("/", response_model=ReviewSchema, status_code=status.HTTP_201_CREATED)
async def create_user(
    review: ReviewCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: UserModel = Depends(get_current_buyer)
):
    """
    Добавляет отзыв от пользователя с ролью 'buyer'.
    """
    # Проверяем есть ли активный товар по переданному id товара в отзыве
    product = await db.scalar(
        select(ProductModel).where(ProductModel.id == review.product_id, ProductModel.is_active == True)
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Проверка нужного диапазона оценки от 1 до 5
    if not 1 <= review.grade <= 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Grade must be between 1 and 5"
        )

    # Добавляем отзыв в бд
    db_review = ReviewModel(**review.model_dump(), user_id=current_user.id)
    db.add(db_review)
    await db.commit()
    await db.refresh(db_review)

    # После добавления - пересчёт рейтинга
    stmt = select(ReviewModel.grade).where(
        ReviewModel.product_id == product.id,
        ReviewModel.is_active == True
    )
    reviews = await db.scalars(stmt)
    reviews = reviews.all()
    if reviews:  # проверка, чтобы не делить на 0
        reviews_avg = sum(reviews) / len(reviews)
        product.rating = reviews_avg
        db.add(product)
        await db.commit()
        await db.refresh(product)
    return db_review


@router.delete("/{review_id}")
async def delete_review(review_id: int,
                        db: AsyncSession = Depends(get_async_db),
                        current_user: UserModel = Depends(get_current_user)
                        ):
    """
        Выполняет мягкое удаление отзыва, если он принадлежит текущему покупателю (только для 'buyer') или admin.
        """
    # Проверяем роль
    if current_user.role not in ('buyer', 'admin'):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    # Проверяем есть ли активный комментарий
    review = await db.scalar(select(ReviewModel).where(ReviewModel.id == review_id, ReviewModel.is_active == True))
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    # Проверяем, что отзыв принадлежит пользователю
    if current_user.id != review.user_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    # Удаляем отзыв
    await db.execute(update(ReviewModel).where(ReviewModel.id == review_id).values(is_active=False))
    await db.commit()
    # Пересчет рейтинга продукта
    product = await db.scalar(
        select(ProductModel).where(ProductModel.id == review.product_id, ProductModel.is_active == True)
    )
    stmt = select(ReviewModel.grade).where(
        ReviewModel.product_id == product.id,
        ReviewModel.is_active == True
    )
    reviews = await db.scalars(stmt)
    reviews = reviews.all()
    if reviews:  # проверка, чтобы не делить на 0
        reviews_avg = sum(reviews) / len(reviews)
        product.rating = reviews_avg
        db.add(product)
        await db.commit()
        await db.refresh(product)
    return {"message": "Review deleted"}


